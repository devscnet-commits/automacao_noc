"""Walk SNMP com o pysnmp simulado: SnmpEngine, transporte e bulk_walk_cmd trocados por
dublês; CommunityData continua o real (para conferir que ele mascara a community)."""

import logging
import traceback

import pytest
from pyasn1.type import univ
from pydantic import SecretStr
from pysnmp.error import PySnmpError
from pysnmp.proto import errind, rfc1902, rfc1905

from app.config import CredenciaisOlt
from app.olt import snmp
from app.olt.base import OltInacessivel, RespostaInesperada
from app.olt.snmp import ParametrosSnmp, walk_tabela

COMMUNITY = "segredo-community"
BASE = "1.3.6.1.4.1.99999.1"  # OID fictício, só para teste
CRED = CredenciaisOlt(snmp_versao="v2c", snmp_community=SecretStr(COMMUNITY))


def linha(sufixo: str, valor):
    return (rfc1902.ObjectName(f"{BASE}.{sufixo}"), valor)


class PysnmpSimulado:
    """Guarda as chamadas e devolve, pelo bulk_walk_cmd, as respostas programadas."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.respostas: list[tuple] = []  # (errorIndication, errorStatus, errorIndex, linhas)
        self.engines: list = []
        self.chamada_walk: tuple | None = None
        self.gerador_fechado = False
        self.erro_transporte: Exception | None = None
        self.transporte: tuple | None = None
        simulado = self

        class MotorFalso:
            def __init__(self) -> None:
                self.fechado = 0
                simulado.engines.append(self)

            def close_dispatcher(self) -> None:
                self.fechado += 1

        class TransporteFalso:
            @classmethod
            async def create(cls, endereco, **opcoes):
                if simulado.erro_transporte:
                    raise simulado.erro_transporte
                simulado.transporte = (endereco, opcoes["timeout"], opcoes["retries"])
                return object()

        async def bulk_walk_cmd(*args, **kwargs):
            simulado.chamada_walk = (args, kwargs)
            try:
                for resposta in simulado.respostas:
                    yield resposta
            finally:
                simulado.gerador_fechado = True

        monkeypatch.setattr(snmp, "SnmpEngine", MotorFalso)
        monkeypatch.setattr(snmp, "UdpTransportTarget", TransporteFalso)
        monkeypatch.setattr(snmp, "bulk_walk_cmd", bulk_walk_cmd)

    @property
    def fechamentos(self) -> int:
        return sum(e.fechado for e in self.engines)


@pytest.fixture
def pysnmp(monkeypatch) -> PysnmpSimulado:
    return PysnmpSimulado(monkeypatch)


def sem_community(texto: str) -> bool:
    return COMMUNITY not in texto


async def walk(**opcoes):
    return await walk_tabela("192.0.2.1", CRED, BASE, **opcoes)


# --- conversão e montagem --------------------------------------------------------------


async def test_converte_tipos_e_monta_sufixos(pysnmp):
    pysnmp.respostas = [
        (
            None,
            0,
            0,
            [
                linha("1.4194304000.0", rfc1902.Integer32(-1950)),
                linha("2.4194304000.0", rfc1902.OctetString(b"CX01-P3")),
                linha("3.4194304000.0", rfc1902.OctetString(b"HWTC\x00\x00\x00\x07")),
            ],
        ),
        (
            None,
            0,
            0,
            [
                linha("4.1", rfc1902.Gauge32(1007)),
                linha("5.1", rfc1902.Counter64(2**40)),
                linha("6.1", univ.ObjectIdentifier("1.3.6.1.2.1")),  # OID vem como pyasn1 puro
                linha("7.1", rfc1902.IpAddress("192.0.2.9")),
                linha("8.1", rfc1902.TimeTicks(500)),
                linha("9.1", rfc1902.Unsigned32(3)),
            ],
        ),
    ]

    resultado = await walk()

    assert resultado == {
        "1.4194304000.0": -1950,
        "2.4194304000.0": b"CX01-P3",
        "3.4194304000.0": b"HWTC\x00\x00\x00\x07",
        "4.1": 1007,
        "5.1": 2**40,
        "6.1": "1.3.6.1.2.1",
        "7.1": b"\xc0\x00\x02\t",
        "8.1": 500,
        "9.1": 3,
    }
    assert pysnmp.fechamentos == 1
    assert pysnmp.gerador_fechado


async def test_tabela_vazia_devolve_dicionario_vazio(pysnmp):
    assert await walk() == {}
    assert pysnmp.fechamentos == 1


async def test_parametros_chegam_ao_pysnmp(pysnmp):
    await walk_tabela(
        "192.0.2.1",
        CRED,
        BASE,
        porta=1161,
        parametros=ParametrosSnmp(timeout=2.5, tentativas=0, max_repeticoes=7),
    )
    args, kwargs = pysnmp.chamada_walk
    assert pysnmp.transporte == (("192.0.2.1", 1161), 2.5, 0)
    assert args[4:6] == (0, 7)  # nonRepeaters, maxRepetitions
    assert kwargs == {"lexicographicMode": False, "lookupMib": False}
    assert args[1].message_processing_model == 1  # SNMPv2c


def test_padroes_conservadores():
    assert ParametrosSnmp() == ParametrosSnmp(timeout=5.0, tentativas=1, max_repeticoes=10)


# --- erros: param no primeiro e sempre fecha o dispatcher -----------------------------------


async def test_para_no_primeiro_erro_de_comunicacao(pysnmp):
    pysnmp.respostas = [
        (None, 0, 0, [linha("1.0", rfc1902.Integer32(1))]),
        (errind.requestTimedOut, 0, 0, []),
        (None, 0, 0, [linha("1.1", rfc1902.Integer32(2))]),  # não pode ser lida
    ]
    with pytest.raises(OltInacessivel, match="No SNMP response"):
        await walk()
    assert pysnmp.fechamentos == 1
    assert pysnmp.gerador_fechado  # o walk do pysnmp continuaria tentando; nós paramos


@pytest.mark.parametrize(
    ("codigo", "erro"),
    [
        (6, OltInacessivel),  # noAccess
        (16, OltInacessivel),  # authorizationError
        (5, RespostaInesperada),  # genErr
        (13, RespostaInesperada),  # resourceUnavailable
    ],
)
async def test_status_de_erro_do_agente(pysnmp, codigo, erro):
    pysnmp.respostas = [(None, rfc1905.errorStatus.clone(codigo), 1, [])]
    with pytest.raises(erro):
        await walk()
    assert pysnmp.fechamentos == 1


async def test_tipo_inesperado(pysnmp):
    pysnmp.respostas = [(None, 0, 0, [linha("1.0", univ.Real(1.5))])]
    with pytest.raises(RespostaInesperada, match="Real"):
        await walk()
    assert pysnmp.fechamentos == 1


async def test_falha_no_transporte(pysnmp):
    pysnmp.erro_transporte = PySnmpError(f"falhou com {COMMUNITY}")  # mensagem nunca repassada
    with pytest.raises(OltInacessivel) as erro:
        await walk()
    assert pysnmp.fechamentos == 1
    assert erro.value.__cause__ is None
    assert sem_community(str(erro.value))


@pytest.mark.parametrize(
    ("cred", "erro"),
    [
        (CredenciaisOlt(snmp_versao="v3"), NotImplementedError),
        (CredenciaisOlt(snmp_community=SecretStr(COMMUNITY)), ValueError),  # sem versão
        (CredenciaisOlt(snmp_versao="v2c"), ValueError),  # sem community
    ],
    ids=["v3", "sem-versao", "sem-community"],
)
async def test_credencial_invalida_falha_antes_de_criar_o_engine(pysnmp, cred, erro):
    with pytest.raises(erro):
        await walk_tabela("192.0.2.1", cred, BASE)
    assert pysnmp.engines == []


# --- community fora de repr, mensagens e logs ----------------------------------------------


async def test_community_nao_aparece_em_repr_mensagens_nem_logs(pysnmp, caplog):
    caplog.set_level(logging.DEBUG)
    pysnmp.respostas = [
        (None, 0, 0, [linha("1.0", rfc1902.Integer32(1))]),
        (errind.requestTimedOut, 0, 0, []),
    ]
    with pytest.raises(OltInacessivel) as erro:
        await walk()

    args, _ = pysnmp.chamada_walk
    assert sem_community(repr(args[1]))  # CommunityData real
    assert sem_community(repr(CRED))
    texto = repr(erro.value) + str(erro.value) + "".join(traceback.format_exception(erro.value))
    assert sem_community(texto)
    assert sem_community(caplog.text)
