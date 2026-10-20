"""Walk SNMP genérico (GETBULK, pysnmp 7). Somente leitura: nada de SET.

A community nunca aparece em log, exceção ou repr: chega dentro de CredenciaisOlt como
SecretStr, só é aberta para montar o CommunityData (cujo repr a mascara), e os erros
levantados aqui não carregam texto livre vindo do pysnmp, só as indicações de erro dele.
"""

import logging
from contextlib import aclosing
from dataclasses import dataclass

from pyasn1.type import univ
from pysnmp.error import PySnmpError
from pysnmp.hlapi.v3arch.asyncio import (
    CommunityData,
    ContextData,
    ObjectIdentity,
    ObjectType,
    SnmpEngine,
    UdpTransportTarget,
    bulk_walk_cmd,
)
from pysnmp.proto import rfc1902

from app.config import CredenciaisOlt
from app.olt.base import OltInacessivel, RespostaInesperada

log = logging.getLogger(__name__)

# errorStatus do agente que indicam credencial recusada (RFC 3416): noAccess, authorizationError.
STATUS_ACESSO_NEGADO = {6, 16}

# int: tipos inteiros (Integer32, Gauge32, Counter64...); bytes: OctetString, que o driver
# decodifica (nome em texto, serial às vezes binário); str: OID.
Valor = int | bytes | str


@dataclass(frozen=True)
class ParametrosSnmp:
    """Valores conservadores por padrão: a OLT é de produção."""

    timeout: float = 5.0  # segundos de espera por resposta, a cada tentativa
    tentativas: int = 1  # novas tentativas depois de um timeout (total = 1 + tentativas)
    max_repeticoes: int = 10  # linhas por GETBULK; poucas, para não pesar na CPU da OLT


async def walk_tabela(
    host: str,
    credenciais: CredenciaisOlt,
    oid_base: str,
    *,
    porta: int = 161,
    parametros: ParametrosSnmp = ParametrosSnmp(),  # noqa: B008 (dataclass imutável)
) -> dict[str, Valor]:
    """Percorre a tabela sob oid_base e devolve {sufixo_do_indice: valor}.

    O sufixo é o OID da linha sem o oid_base, ex.: "4194304000.3". Tabela vazia ou OID
    inexistente devolvem {}: cabe ao driver decidir se isso é RespostaInesperada.
    Falha de comunicação ou credencial recusada levantam OltInacessivel.
    """
    community = _community_v2c(credenciais, host)
    base = rfc1902.ObjectName(oid_base)
    destino = f"{host}:{porta}"
    engine = SnmpEngine()
    try:
        try:
            alvo = await UdpTransportTarget.create(
                (host, porta), timeout=parametros.timeout, retries=parametros.tentativas
            )
        except PySnmpError as erro:
            raise OltInacessivel(
                f"{destino}: transporte SNMP falhou ({type(erro).__name__})"
            ) from None

        respostas = bulk_walk_cmd(
            engine,
            # mpModel=1: SNMPv2c. S508 (v2c é inseguro): decisão atual; v3 está PENDENTE.
            CommunityData(community, mpModel=1),  # noqa: S508
            alvo,
            ContextData(),
            0,
            parametros.max_repeticoes,
            ObjectType(ObjectIdentity(base)),
            lexicographicMode=False,  # para ao sair da tabela
            lookupMib=False,
        )
        resultado: dict[str, Valor] = {}
        async with aclosing(respostas):
            async for indicacao, status, indice, linhas in respostas:
                if indicacao:
                    # Inclui timeout; em v2c, community errada também vira timeout.
                    raise OltInacessivel(f"{destino}: {indicacao}") from None
                if status:
                    nome_status = status.prettyPrint()
                    if int(status) in STATUS_ACESSO_NEGADO:
                        raise OltInacessivel(f"{destino}: acesso negado ({nome_status})")
                    raise RespostaInesperada(
                        f"{destino}: erro SNMP {nome_status} (índice {indice}) em {oid_base}"
                    )
                for nome, valor in linhas:
                    sufixo = ".".join(str(n) for n in tuple(nome)[len(base) :])
                    resultado[sufixo] = _converter(valor, destino, oid_base)
    finally:
        engine.close_dispatcher()

    log.debug("walk %s %s: %d linhas", destino, oid_base, len(resultado))
    return resultado


def _community_v2c(credenciais: CredenciaisOlt, host: str) -> str:
    if credenciais.snmp_versao == "v3":
        raise NotImplementedError("PENDENTE: validar com NOC/bancada. SNMPv3 não implementado")
    if credenciais.snmp_versao != "v2c":
        raise ValueError(f"versão SNMP não configurada para {host}")
    if credenciais.snmp_community is None:
        raise ValueError(f"community SNMP não configurada para {host}")
    return credenciais.snmp_community.get_secret_value()


def _converter(valor: object, destino: str, oid_base: str) -> Valor:
    # Bases do pyasn1: o pysnmp devolve tanto os tipos dele (rfc1902) quanto os do pyasn1
    # puros (um OID vem como univ.ObjectIdentifier, não como rfc1902.ObjectIdentifier).
    if isinstance(valor, univ.OctetString):  # inclui IpAddress, Bits e Opaque
        return bytes(valor)
    if isinstance(valor, univ.Integer):  # Integer32, Gauge32, Counter32/64, TimeTicks...
        return int(valor)
    if isinstance(valor, univ.ObjectIdentifier):
        return str(valor)
    raise RespostaInesperada(
        f"{destino}: tipo SNMP inesperado {type(valor).__name__} em {oid_base}"
    )
