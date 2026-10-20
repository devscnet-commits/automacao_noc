"""Cliente MK contra um MK simulado (httpx.MockTransport)."""

import asyncio
import logging
import traceback
from collections import Counter
from datetime import datetime

import httpx
import pytest
from pydantic import SecretStr

from app.mk import cliente as modulo
from app.mk.cliente import (
    FUSO_MK,
    ClienteMk,
    ConfiguracaoMkInvalida,
    ErroDoMk,
    ErroMk,
    MkInacessivel,
    RespostaMkInesperada,
)

URL = "https://mk.teste"
TOKEN_USUARIO = "segredo-token-usuario"
CONTRA_SENHA = "segredo-contra-senha"
EXPIRE = "20/10/2026 11:00:00"
SEGREDOS = [TOKEN_USUARIO, CONTRA_SENHA, "SESSAO-"]


class MkSimulado:
    """Responde à autenticação e às consultas; conta logins, requisições e simultâneas."""

    def __init__(self, *, mesmo_token_no_relogin: bool = False, demora: float = 0.0) -> None:
        self.mesmo_token = mesmo_token_no_relogin
        self.demora = demora
        self.logins = 0
        self.por_token: Counter[str] = Counter()
        self.em_andamento = 0
        self.pico = 0
        self.autenticacoes: list[dict[str, str]] = []
        self.resposta: httpx.Response | Exception | None = None  # força a resposta das consultas
        self.expire = EXPIRE

    async def __call__(self, req: httpx.Request) -> httpx.Response:
        self.em_andamento += 1
        self.pico = max(self.pico, self.em_andamento)
        try:
            await asyncio.sleep(self.demora)
            if req.url.path == "/mk/WSAutenticacao.rule":
                self.logins += 1
                self.autenticacoes.append(dict(req.url.params))
                token = "SESSAO-UNICA" if self.mesmo_token else f"SESSAO-{self.logins}"
                corpo = {"status": "OK", "Token": token, "Expire": self.expire, "LimiteUso": 10}
                return httpx.Response(200, json=corpo)
            self.por_token[req.url.params["token"]] += 1
            if isinstance(self.resposta, Exception):
                raise self.resposta
            return self.resposta or httpx.Response(200, json={"status": "OK", "dados": [1]})
        finally:
            self.em_andamento -= 1


class Relogio:
    def __init__(self) -> None:
        self.agora = datetime(2026, 10, 20, 10, 0, tzinfo=FUSO_MK)

    def __call__(self) -> datetime:
        return self.agora


def novo_cliente(mk: MkSimulado, relogio: Relogio | None = None, **opcoes) -> ClienteMk:
    return ClienteMk(
        URL,
        SecretStr(TOKEN_USUARIO),
        SecretStr(CONTRA_SENHA),
        transport=httpx.MockTransport(mk),
        relogio=relogio or Relogio(),
        **opcoes,
    )


def limpo(texto: str) -> bool:
    return not any(s in texto for s in SEGREDOS) and "mk.teste" not in texto


def texto_do_erro(erro: BaseException) -> str:
    return repr(erro) + str(erro) + "".join(traceback.format_exception(erro))


# --- autenticação e token ---------------------------------------------------------------


async def test_autentica_e_consulta():
    mk = MkSimulado()
    async with novo_cliente(mk) as c:
        corpo = await c.consultar("WSServico", {"cpf": "123"})
    assert corpo["dados"] == [1]
    assert mk.autenticacoes == [
        {"sys": "MK0", "token": TOKEN_USUARIO, "password": CONTRA_SENHA, "cd_servico": "9999"}
    ]
    assert mk.por_token == {"SESSAO-1": 1}
    assert c.limite_uso == 10


async def test_reaproveita_o_token_e_renova_perto_do_expire():
    mk, relogio = MkSimulado(), Relogio()
    async with novo_cliente(mk, relogio) as c:
        await c.consultar("WSServico")
        relogio.agora = datetime(2026, 10, 20, 10, 57, 59, tzinfo=FUSO_MK)  # faltam 2min01s
        await c.consultar("WSServico")
        assert mk.logins == 1
        relogio.agora = datetime(2026, 10, 20, 10, 58, 30, tzinfo=FUSO_MK)  # faltam 1min30s
        await c.consultar("WSServico")
        assert mk.logins == 2
    assert mk.por_token == {"SESSAO-1": 2, "SESSAO-2": 1}


async def test_20_consultas_concorrentes_sem_token_fazem_1_login():
    mk = MkSimulado(demora=0.01)
    async with novo_cliente(mk) as c:
        await asyncio.wait_for(
            asyncio.gather(*[c.consultar("WSServico") for _ in range(20)]), timeout=5
        )
    assert mk.logins == 1
    assert mk.por_token == {"SESSAO-1": 20}


@pytest.mark.parametrize("mesmo_token", [False, True], ids=["token-novo", "mesmo-token"])
async def test_token_expirado_com_vagas_esgotadas_nao_trava(monkeypatch, mesmo_token):
    """8 consultas, 4 vagas, todas recebem "token expirado": a reautenticação precisa de vaga.
    Se a consulta segurasse a vaga ao reautenticar, travaria; o wait_for transforma o
    travamento em falha."""
    mk = MkSimulado(mesmo_token_no_relogin=mesmo_token, demora=0.05)
    async with novo_cliente(mk, max_simultaneas=4) as c:
        await c.consultar("WSServico")  # login inicial
        mk.por_token.clear()
        logins_antes = mk.logins
        monkeypatch.setattr(modulo, "token_expirado", lambda corpo: True)

        await asyncio.wait_for(
            asyncio.gather(*[c.consultar("WSServico") for _ in range(8)]), timeout=5
        )

    assert mk.logins - logins_antes == 1  # 1 reautenticação
    assert sum(mk.por_token.values()) == 16  # cada consulta repetida uma vez
    if not mesmo_token:
        assert mk.por_token == {"SESSAO-1": 8, "SESSAO-2": 8}
    assert mk.pico <= 4


async def test_limite_de_simultaneas():
    mk = MkSimulado(demora=0.02)
    async with novo_cliente(mk, max_simultaneas=4) as c:
        await asyncio.gather(*[c.consultar("WSServico") for _ in range(30)])
    assert mk.pico == 4


async def test_expirado_de_novo_depois_da_repeticao_sobe_o_erro(monkeypatch):
    mk = MkSimulado()
    mk.resposta = httpx.Response(200, json={"status": "ERRO", "Num. ERRO": "7", "Mensagem": "x"})
    monkeypatch.setattr(modulo, "token_expirado", lambda corpo: True)
    async with novo_cliente(mk) as c:
        with pytest.raises(ErroDoMk):
            await c.consultar("WSServico")
    assert sum(mk.por_token.values()) == 2  # tentativa + uma única repetição


async def test_timeout_por_chamada():
    timeouts = []

    async def mk(req: httpx.Request) -> httpx.Response:
        timeouts.append(req.extensions["timeout"]["read"])
        if "WSAutenticacao" in req.url.path:
            return httpx.Response(200, json={"status": "OK", "Token": "T", "Expire": EXPIRE})
        return httpx.Response(200, json={"status": "OK"})

    async with ClienteMk(
        URL, SecretStr("u"), SecretStr("s"), transport=httpx.MockTransport(mk), relogio=Relogio()
    ) as c:
        await c.consultar("WSServico")
        await c.consultar("WSServico", timeout_http=40)
    assert timeouts == [15.0, 15.0, 40]  # login, consulta padrão, consulta com override


# --- erros --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("resposta", "erro_esperado"),
    [
        (
            httpx.Response(200, json={"status": "ERRO", "Num. ERRO": "42", "Mensagem": "falhou"}),
            ErroDoMk,
        ),
        (
            httpx.Response(
                200, text="<html>erro " + "x" * 5000, headers={"content-type": "text/html"}
            ),
            RespostaMkInesperada,
        ),
        (httpx.Response(502, text="bad gateway"), MkInacessivel),
        (httpx.Response(404, text="nao achei"), RespostaMkInesperada),
        (httpx.ConnectError("conexão recusada"), MkInacessivel),
        (httpx.ReadTimeout("demorou"), MkInacessivel),
        (httpx.Response(200, json={"status": "talvez"}), RespostaMkInesperada),
        (httpx.Response(200, json={"sem": "status"}), RespostaMkInesperada),
        (httpx.Response(200, json=[1, 2]), RespostaMkInesperada),
    ],
    ids=[
        "status-erro",
        "html",
        "http-5xx",
        "http-404",
        "conexao",
        "timeout",
        "status-desconhecido",
        "sem-status",
        "lista-na-raiz",
    ],
)
async def test_erros_viram_excecoes_proprias(resposta, erro_esperado):
    mk = MkSimulado()
    mk.resposta = resposta
    async with novo_cliente(mk) as c:
        with pytest.raises(erro_esperado) as erro:
            await c.consultar("WSServico")
    assert limpo(texto_do_erro(erro.value))
    assert "x" * 100 not in str(erro.value)  # o corpo da página não vai para a mensagem


@pytest.mark.parametrize("status", ["OK", "ok", "sucesso", "SUCESSO", True])
async def test_formatos_de_sucesso(status):
    mk = MkSimulado()
    mk.resposta = httpx.Response(200, json={"status": status})
    async with novo_cliente(mk) as c:
        await c.consultar("WSServico")


async def test_erro_do_mk_com_segredo_na_mensagem_e_mascarado():
    mk = MkSimulado()
    mk.resposta = httpx.Response(
        200,
        json={"status": "ERRO", "Num. ERRO": "9", "Mensagem": f"token SESSAO-1 e {TOKEN_USUARIO}"},
    )
    async with novo_cliente(mk) as c:
        with pytest.raises(ErroDoMk) as erro:
            await c.consultar("WSServico")
    assert erro.value.numero == "9"
    assert erro.value.mensagem == "token *** e ***"
    assert limpo(texto_do_erro(erro.value))


async def test_expire_invalido():
    mk = MkSimulado()
    mk.expire = "2026-10-20 11:00"
    async with novo_cliente(mk) as c:
        with pytest.raises(RespostaMkInesperada, match="Expire"):
            await c.consultar("WSServico")


@pytest.mark.parametrize(
    ("base_url", "token", "senha", "mensagem"),
    [
        ("http://mk.teste", SecretStr("a"), SecretStr("b"), "https"),
        (None, SecretStr("a"), SecretStr("b"), "NOC_MK_BASE_URL"),
        (URL, None, SecretStr("b"), "NOC_MK_TOKEN_USUARIO"),
        (URL, SecretStr("a"), None, "NOC_MK_CONTRA_SENHA"),
    ],
    ids=["http", "sem-url", "sem-token", "sem-contra-senha"],
)
def test_configuracao_invalida_recusada_na_criacao(base_url, token, senha, mensagem):
    with pytest.raises(ConfiguracaoMkInvalida, match=mensagem):
        ClienteMk(base_url, token, senha)


@pytest.mark.parametrize(
    ("servico", "parametros"), [("../WSX", None), ("WSServico", {"token": "x"})]
)
async def test_argumentos_invalidos(servico, parametros):
    async with novo_cliente(MkSimulado()) as c:
        with pytest.raises(ValueError):
            await c.consultar(servico, parametros)


# --- logs ---------------------------------------------------------------------------------


async def test_nenhum_segredo_nem_url_nos_logs(caplog, monkeypatch):
    caplog.set_level(logging.DEBUG)
    mk = MkSimulado()
    async with novo_cliente(mk) as c:
        await c.consultar("WSServico", {"cpf": "123"})
        monkeypatch.setattr(modulo, "token_expirado", lambda corpo: True)
        await c.consultar("WSServico")  # passa pelo log de reautenticação
        monkeypatch.setattr(modulo, "token_expirado", lambda corpo: False)
        for resposta in [httpx.ConnectError("x"), httpx.Response(500)]:
            mk.resposta = resposta
            with pytest.raises(ErroMk):
                await c.consultar("WSServico")

    assert caplog.records  # houve log
    assert limpo(caplog.text)
    assert "token=" not in caplog.text
    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
