"""Interpretação do nome da ONU na OLT: extrai a caixa (CTO) e a porta da caixa.

O nome vem bruto de OnuLida.nome_na_olt. A interpretação é plugável por fabricante, porque a
convenção pode ser diferente entre Huawei e Datacom.

interpretar_nome() nunca levanta exceção: nome fora do padrão (ou erro no interpretador)
volta como "não reconhecido", com o motivo. Na coleta, isso vira divergência, não erro.

O nome contém dado pessoal (nome do cliente, contrato): nunca vai para log. No log, a ONU é
identificada por OLT + PON + onu_id (ReferenciaOnu). No banco (detalhe da divergência), pode.
"""

import logging
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.olt.base import Fabricante, PosicaoPon

log = logging.getLogger(__name__)

MotivoNaoReconhecido = Literal[
    "nome_vazio",
    "convencao_pendente",
    "fora_do_padrao",
    "fabricante_sem_interpretador",
    "erro_no_interpretador",
]


@dataclass(frozen=True)
class ReferenciaOnu:
    """Como a ONU aparece em log: sem dado pessoal."""

    olt: str
    posicao: PosicaoPon
    onu_id: int

    def __str__(self) -> str:
        p = self.posicao
        return f"{self.olt} {p.chassi}/{p.slot}/{p.porta} onu {self.onu_id}"


@dataclass(frozen=True)
class NomeInterpretado:
    """Reconhecido (caixa e porta preenchidas) ou não reconhecido (motivo preenchido).

    Campos com dado pessoal ficam fora do repr, para não vazarem se o objeto for logado.
    """

    nome_original: str | None = field(repr=False)
    caixa: str | None = None  # como em caixas.identificador
    porta: int | None = None  # como em onus.porta_caixa
    motivo: MotivoNaoReconhecido | None = None
    detalhe: str | None = field(default=None, repr=False)  # vai para a divergência, no banco
    # PENDENTE: validar com NOC/bancada se a convenção traz o nome do cliente e o código do
    # contrato. O contrato ligaria a ONU ao cadastro do MK.
    cliente: str | None = field(default=None, repr=False)
    contrato: str | None = field(default=None, repr=False)

    def __post_init__(self) -> None:
        reconhecido = self.caixa is not None and self.porta is not None and self.motivo is None
        nao_reconhecido = self.caixa is None and self.porta is None and self.motivo is not None
        if not (reconhecido or nao_reconhecido):
            raise ValueError("NomeInterpretado precisa de caixa e porta, ou de um motivo")

    @property
    def reconhecido(self) -> bool:
        return self.motivo is None

    @classmethod
    def com_caixa(
        cls,
        nome: str,
        caixa: str,
        porta: int,
        *,
        cliente: str | None = None,
        contrato: str | None = None,
    ) -> "NomeInterpretado":
        return cls(nome, caixa=caixa, porta=porta, cliente=cliente, contrato=contrato)

    @classmethod
    def nao_reconhecido(
        cls, nome: str | None, motivo: MotivoNaoReconhecido, detalhe: str | None = None
    ) -> "NomeInterpretado":
        return cls(nome, motivo=motivo, detalhe=detalhe)


# Recebe o nome (não vazio) e devolve o resultado. Pode levantar exceção à vontade:
# interpretar_nome() a transforma em "não reconhecido". Não deve logar o nome.
Interpretador = Callable[[str], NomeInterpretado]


def interpretar_huawei(nome: str) -> NomeInterpretado:
    """PENDENTE: validar com NOC/bancada a convenção de nome das ONUs nas OLTs Huawei."""
    return NomeInterpretado.nao_reconhecido(nome, "convencao_pendente")


def interpretar_datacom(nome: str) -> NomeInterpretado:
    """PENDENTE: validar com NOC/bancada a convenção de nome das ONUs nas OLTs Datacom."""
    return NomeInterpretado.nao_reconhecido(nome, "convencao_pendente")


INTERPRETADORES: dict[Fabricante, Interpretador] = {
    "huawei": interpretar_huawei,
    "datacom": interpretar_datacom,
}


def interpretar_nome(
    fabricante: Fabricante, nome: str | None, *, referencia: ReferenciaOnu
) -> NomeInterpretado:
    """Caixa e porta a partir do nome da ONU na OLT. Nunca levanta exceção.

    referencia identifica a ONU nos logs, no lugar do nome.
    """
    if nome is None or not nome.strip():
        return NomeInterpretado.nao_reconhecido(nome, "nome_vazio")
    interpretador = INTERPRETADORES.get(fabricante)
    if interpretador is None:
        return NomeInterpretado.nao_reconhecido(
            nome, "fabricante_sem_interpretador", f"fabricante {fabricante!r}"
        )
    try:
        return interpretador(nome)
    except Exception as erro:  # nome fora do padrão nunca derruba a coleta
        # Sem exc_info: a mensagem da exceção pode conter o nome. Só tipo e local do erro.
        local = traceback.extract_tb(erro.__traceback__)[-1]
        log.warning(
            "interpretador %s falhou (%s em %s:%d) para %s",
            fabricante,
            type(erro).__name__,
            local.filename,
            local.lineno,
            referencia,
        )
        return NomeInterpretado.nao_reconhecido(nome, "erro_no_interpretador", type(erro).__name__)
