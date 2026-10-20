"""Testes da interpretação do nome da ONU (app/coleta/nomes.py).

Os casos de convenção real estão marcados PENDENTE: quando o NOC passar exemplos, troque cada
placeholder por um exemplo real e tire o skip. Quando a convenção de um fabricante for
implementada, o teste test_sem_convencao_tudo_e_pendente desse fabricante deve ser removido.
"""

import pytest

from app.coleta import nomes
from app.coleta.nomes import NomeInterpretado, ReferenciaOnu, interpretar_nome
from app.olt.base import PosicaoPon

FABRICANTES = ["huawei", "datacom"]
REF = ReferenciaOnu(olt="OLT-TESTE", posicao=PosicaoPon(0, 1, 2), onu_id=7)
# Nome fictício com cara de dado pessoal, para os testes de vazamento.
NOME_PESSOAL = "CX01-P3 MARIA DA SILVA CONTRATO 123456"

PENDENTE = pytest.mark.skip(reason="PENDENTE: aguardando exemplos reais do NOC")

# (nome como está na OLT, caixa esperada, porta esperada)
RECONHECIDOS = {
    "huawei": [
        pytest.param("<exemplo Huawei 1>", "<caixa>", 0, marks=PENDENTE, id="huawei-pendente-1"),
        pytest.param("<exemplo Huawei 2>", "<caixa>", 0, marks=PENDENTE, id="huawei-pendente-2"),
    ],
    "datacom": [
        pytest.param("<exemplo Datacom 1>", "<caixa>", 0, marks=PENDENTE, id="datacom-pendente-1"),
        pytest.param("<exemplo Datacom 2>", "<caixa>", 0, marks=PENDENTE, id="datacom-pendente-2"),
    ],
}

# Nomes reais que o NOC considera fora do padrão: (nome, motivo esperado)
FORA_DO_PADRAO = {
    "huawei": [
        pytest.param("<nome fora do padrão Huawei>", "fora_do_padrao", marks=PENDENTE, id="hw-1"),
    ],
    "datacom": [
        pytest.param("<nome fora do padrão Datacom>", "fora_do_padrao", marks=PENDENTE, id="dc-1"),
    ],
}


@pytest.mark.parametrize(("nome", "caixa", "porta"), RECONHECIDOS["huawei"])
def test_huawei_reconhece(nome, caixa, porta):
    resultado = interpretar_nome("huawei", nome, referencia=REF)
    assert resultado.reconhecido
    assert (resultado.caixa, resultado.porta) == (caixa, porta)


@pytest.mark.parametrize(("nome", "caixa", "porta"), RECONHECIDOS["datacom"])
def test_datacom_reconhece(nome, caixa, porta):
    resultado = interpretar_nome("datacom", nome, referencia=REF)
    assert resultado.reconhecido
    assert (resultado.caixa, resultado.porta) == (caixa, porta)


@pytest.mark.parametrize(("nome", "motivo"), FORA_DO_PADRAO["huawei"])
def test_huawei_fora_do_padrao(nome, motivo):
    resultado = interpretar_nome("huawei", nome, referencia=REF)
    assert not resultado.reconhecido
    assert resultado.motivo == motivo


@pytest.mark.parametrize(("nome", "motivo"), FORA_DO_PADRAO["datacom"])
def test_datacom_fora_do_padrao(nome, motivo):
    resultado = interpretar_nome("datacom", nome, referencia=REF)
    assert not resultado.reconhecido
    assert resultado.motivo == motivo


@pytest.mark.parametrize("fabricante", FABRICANTES)
@pytest.mark.parametrize("nome", [None, "", "   "])
def test_nome_vazio(fabricante, nome):
    resultado = interpretar_nome(fabricante, nome, referencia=REF)
    assert not resultado.reconhecido
    assert resultado.motivo == "nome_vazio"


@pytest.mark.parametrize("fabricante", FABRICANTES)
def test_sem_convencao_tudo_e_pendente(fabricante):
    resultado = interpretar_nome(fabricante, "QUALQUER-NOME-01", referencia=REF)
    assert not resultado.reconhecido
    assert resultado.motivo == "convencao_pendente"
    assert resultado.nome_original == "QUALQUER-NOME-01"


def test_interpretador_que_falha_nao_levanta(monkeypatch):
    def quebrado(nome: str) -> NomeInterpretado:
        raise IndexError("bug no interpretador")

    monkeypatch.setitem(nomes.INTERPRETADORES, "huawei", quebrado)
    resultado = interpretar_nome("huawei", "CX01-P3", referencia=REF)
    assert resultado.motivo == "erro_no_interpretador"
    assert resultado.detalhe == "IndexError"


def test_fabricante_sem_interpretador_nao_levanta():
    resultado = interpretar_nome("zte", "CX01-P3", referencia=REF)
    assert resultado.motivo == "fabricante_sem_interpretador"


def test_resultado_reconhecido_exige_caixa_e_porta():
    assert NomeInterpretado.com_caixa("n", "CX01", 3).reconhecido
    with pytest.raises(ValueError):
        NomeInterpretado(nome_original="n", caixa="CX01")
    with pytest.raises(ValueError):
        NomeInterpretado(nome_original="n", caixa="CX01", porta=3, motivo="fora_do_padrao")


def test_nome_nunca_vai_para_o_log(monkeypatch, caplog):
    def quebrado(nome: str) -> NomeInterpretado:
        raise ValueError(f"não entendi {nome}")  # a mensagem carrega o nome inteiro

    monkeypatch.setitem(nomes.INTERPRETADORES, "datacom", quebrado)
    with caplog.at_level("DEBUG"):
        resultado = interpretar_nome("datacom", NOME_PESSOAL, referencia=REF)

    assert resultado.motivo == "erro_no_interpretador"
    assert "MARIA" not in caplog.text
    assert "123456" not in caplog.text
    assert "OLT-TESTE 0/1/2 onu 7" in caplog.text
    assert all(registro.exc_info is None for registro in caplog.records)


def test_repr_nao_mostra_dado_pessoal():
    resultado = NomeInterpretado.com_caixa(
        NOME_PESSOAL, "CX01", 3, cliente="MARIA", contrato="123456"
    )
    assert "MARIA" not in repr(resultado)
    assert "123456" not in repr(resultado)
    assert "CX01" in repr(resultado)


def test_cliente_e_contrato_sao_opcionais():
    sem = NomeInterpretado.com_caixa("n", "CX01", 3)
    com = NomeInterpretado.com_caixa("n", "CX01", 3, cliente="MARIA", contrato="123456")
    assert (sem.cliente, sem.contrato) == (None, None)
    assert (com.cliente, com.contrato) == ("MARIA", "123456")
