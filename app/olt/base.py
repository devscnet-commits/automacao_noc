"""Interface de leitura das OLTs.

Princípio central: o sistema LÊ as OLTs, mas NUNCA escreve nelas; toda escrita passa pelo
MK ERP. Por isso a interface só tem métodos de leitura, e nenhum driver pode acrescentar
outro tipo de operação (nada de SNMP SET nem comando de configuração na CLI).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Literal, get_args

from app.config import CredenciaisOlt

# Mesmos valores do CHECK de olts.fabricante.
Fabricante = Literal["huawei", "datacom"]

# As três medidas de potência, com o mesmo significado das colunas de leituras_sinal.
Medida = Literal["rx_onu", "tx_onu", "rx_olt"]


class ErroOlt(Exception):
    """Base dos erros de leitura de OLT. Todo driver só lança erros desta hierarquia.

    Nenhuma mensagem pode conter community, senha ou usuário.
    """


class OltInacessivel(ErroOlt):
    """Não deu para falar com a OLT: timeout, sem resposta ou credencial recusada."""


class RespostaInesperada(ErroOlt):
    """A OLT respondeu, mas num formato que o driver não entende (OID ausente, valor fora do
    padrão)."""


@dataclass(frozen=True)
class DadosOlt:
    """O necessário para alcançar uma OLT (espelha a tabela olts). Sem credenciais."""

    nome: str
    fabricante: Fabricante
    endereco_ip: IPv4Address | IPv6Address
    snmp_porta: int = 161
    ssh_porta: int = 22

    def __post_init__(self) -> None:
        if self.fabricante not in get_args(Fabricante):
            raise ValueError(f"fabricante desconhecido: {self.fabricante!r}")


@dataclass(frozen=True)
class PosicaoPon:
    """Porta PON. Huawei chama o primeiro nível de frame (0/1/0); Datacom, de chassi (1/1/1)."""

    chassi: int
    slot: int
    porta: int


@dataclass(frozen=True)
class OnuLida:
    """ONU provisionada, como a OLT a mostra no momento da leitura."""

    posicao: PosicaoPon
    onu_id: int
    lida_em: datetime  # com fuso horário
    serial: str | None
    # Nome/descrição da ONU exatamente como veio da OLT, sem interpretar. É daqui que
    # app/coleta/nomes.py extrai caixa e porta.
    nome_na_olt: str | None
    # PENDENTE: validar com NOC/bancada os valores de status de cada fabricante. Até lá,
    # guarda o valor como veio da OLT, sem interpretar.
    status_bruto: str | None
    # Já convertidas para dBm. None = não lida ou ONU offline.
    rx_onu_dbm: float | None
    tx_onu_dbm: float | None
    rx_olt_dbm: float | None


@dataclass(frozen=True)
class OnuDescoberta:
    """ONU detectada pela OLT e ainda não provisionada (autofind)."""

    posicao: PosicaoPon
    serial: str
    lida_em: datetime  # com fuso horário
    # PENDENTE: validar com NOC/bancada quais outros campos o autofind de cada fabricante traz.


class DriverOLT(ABC):
    """Leitura de uma OLT. Uma instância por OLT."""

    def __init__(self, olt: DadosOlt, credenciais: CredenciaisOlt) -> None:
        self.olt = olt
        self.credenciais = credenciais

    @abstractmethod
    async def listar_onus(self, posicao: PosicaoPon | None = None) -> list[OnuLida]:
        """ONUs provisionadas, com o sinal de cada uma: só as da PON indicada ou, sem filtro,
        as da OLT inteira. Quando só uma PON interessa, filtre: ler a OLT inteira pesa nela."""

    @abstractmethod
    async def ler_onu(self, posicao: PosicaoPon, onu_id: int) -> OnuLida | None:
        """Uma ONU. None se não houver ONU provisionada nessa posição."""

    @abstractmethod
    async def listar_autofind(self) -> list[OnuDescoberta]:
        """ONUs detectadas pela OLT e ainda não provisionadas."""
