"""Leitura das OLTs Huawei (MA5600T/MA5800). Ainda não implementado."""

from app.olt.base import DriverOLT, Medida, OnuDescoberta, OnuLida, PosicaoPon

# PENDENTE: validar com NOC/bancada. OIDs SNMP das ONUs, e se mudam entre MA5600T e MA5800.
OID_ONU_NOME: str | None = None
OID_ONU_SERIAL: str | None = None
OID_ONU_STATUS: str | None = None
OID_ONU_RX_ONU: str | None = None
OID_ONU_TX_ONU: str | None = None
OID_ONU_RX_OLT: str | None = None

# PENDENTE: validar com NOC/bancada. Comando de CLI (somente leitura) que lista o autofind.
COMANDO_AUTOFIND: str | None = None


def converter_potencia(medida: Medida, bruto: int) -> float | None:
    """Valor bruto do SNMP -> dBm; None para o valor sentinela de ONU offline.

    PENDENTE: validar com NOC/bancada a escala (e eventual deslocamento) de cada medida e o
    valor sentinela de ONU offline.
    """
    raise NotImplementedError("PENDENTE: escala de potência da Huawei não validada")


class DriverHuawei(DriverOLT):
    async def listar_onus(self) -> list[OnuLida]:
        raise NotImplementedError("PENDENTE: OIDs da Huawei não validados")

    async def ler_onu(self, posicao: PosicaoPon, onu_id: int) -> OnuLida | None:
        raise NotImplementedError("PENDENTE: OIDs da Huawei não validados")

    async def listar_autofind(self) -> list[OnuDescoberta]:
        raise NotImplementedError("PENDENTE: comando de autofind da Huawei não validado")
