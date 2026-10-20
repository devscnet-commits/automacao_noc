"""Cliente HTTP do MK ERP. Neste passo, só leitura: autenticação e consulta genérica.

Regras da API (confirmadas na documentação do MK):
- autenticação: GET /mk/WSAutenticacao.rule?sys=MK0&token=<token_usuario>
  &password=<contra_senha>&cd_servico=9999, que devolve Token e Expire, entre outros;
- demais chamadas: GET /mk/<Servico>.rule?sys=MK0&token=<Token>&<parametros>;
- erros voltam com HTTP 200 e {"status": "ERRO", "Mensagem": ..., "Num. ERRO": ...}.

Token e contra-senha vão na query string, então: o logger do httpx fica em WARNING, nenhum
log nosso tem a URL, e nenhuma exceção levantada aqui carrega a URL ou o corpo da resposta.
Toda falha vira uma exceção de ErroMk, exceto configuração inválida (ConfiguracaoMkInvalida).
"""

import asyncio
import json
import logging
import re
import ssl
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from pydantic import SecretStr

from app.config import Configuracao

# O httpx registra a URL completa (com token e contra-senha) em INFO e o httpcore, detalhes da
# conexão em DEBUG: os dois ficam em WARNING, seja qual for o nível da aplicação.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

log = logging.getLogger(__name__)

FUSO_MK = ZoneInfo("America/Sao_Paulo")  # o Expire vem no horário de Brasília
FORMATO_EXPIRE = "%d/%m/%Y %H:%M:%S"
MARGEM_RENOVACAO = timedelta(minutes=2)
# Leitura costuma ser rápida. Escrita (futura) é bem mais lenta: a documentação do MK mostra
# de 8 s a mais de 30 s, então o método de escrita vai precisar de um timeout próprio, maior.
TIMEOUT_CONSULTA = 15.0
# O MK é o ERP de produção dos atendentes: o sistema não pode competir com eles.
MAX_SIMULTANEAS = 4
SISTEMA = "MK0"
SERVICO_AUTENTICACAO = "WSAutenticacao"
CD_SERVICO_AUTENTICACAO = "9999"
PARAMETROS_RESERVADOS = {"sys", "token"}
NOME_DE_SERVICO = re.compile(r"[A-Za-z0-9_]+")

Corpo = dict[str, Any]


class ConfiguracaoMkInvalida(ValueError):
    """Falta credencial do MK ou a URL não é HTTPS. A mensagem cita variáveis, nunca valores."""


class ErroMk(Exception):
    """Base dos erros ao falar com o MK. Nenhuma mensagem contém URL, token ou senha."""


class MkInacessivel(ErroMk):
    """Não deu para falar com o MK: rede, timeout, certificado recusado ou HTTP 5xx."""


class RespostaMkInesperada(ErroMk):
    """O MK respondeu fora do formato esperado: não é JSON, status desconhecido, campo faltando."""


class ErroDoMk(ErroMk):
    """O MK respondeu com status ERRO."""

    def __init__(self, servico: str, numero: str | None, mensagem: str | None) -> None:
        self.servico = servico
        self.numero = numero
        self.mensagem = mensagem
        super().__init__(f"{servico}: MK devolveu ERRO {numero}: {mensagem}")


class ClienteMk:
    """Cliente de leitura do MK. Uso:

    async with ClienteMk.da_configuracao(obter_configuracao()) as mk:
        corpo = await mk.consultar("WSAlgumServico", {"parametro": "valor"})
    """

    def __init__(
        self,
        base_url: str | None,
        token_usuario: SecretStr | None,
        contra_senha: SecretStr | None,
        *,
        ca_bundle: Path | None = None,
        timeout: float = TIMEOUT_CONSULTA,
        max_simultaneas: int = MAX_SIMULTANEAS,
        transport: httpx.AsyncBaseTransport | None = None,  # testes: httpx.MockTransport
        relogio: Callable[[], datetime] = lambda: datetime.now(FUSO_MK),
    ) -> None:
        faltando = [
            variavel
            for variavel, valor in (
                ("NOC_MK_BASE_URL", base_url),
                ("NOC_MK_TOKEN_USUARIO", token_usuario),
                ("NOC_MK_CONTRA_SENHA", contra_senha),
            )
            if not valor
        ]
        if faltando:
            raise ConfiguracaoMkInvalida("configuração do MK incompleta: " + ", ".join(faltando))
        url = httpx.URL(str(base_url))
        if url.scheme != "https":
            # Token e contra-senha vão na query string: em HTTP puro, trafegariam abertos.
            raise ConfiguracaoMkInvalida("NOC_MK_BASE_URL precisa ser https://")

        self._token_usuario = token_usuario
        self._contra_senha = contra_senha
        self._relogio = relogio
        self._lock = asyncio.Lock()
        # Teto de requisições em andamento ao MK (inclusive autenticação), somando todas as
        # chamadas concorrentes deste cliente.
        self._vagas = asyncio.Semaphore(max_simultaneas)
        self._token: SecretStr | None = None
        # Sobe a cada login. Quem recebeu "token expirado" compara a geração que usou com a
        # atual para saber se outra chamada já renovou; comparar o valor do token não serve,
        # porque o MK pode devolver o mesmo token num novo login.
        self._geracao = 0
        self._expira_em: datetime | None = None
        # Do último login, como vieram (formato ainda não validado).
        self.limite_uso: Any = None
        self.servicos_autorizados: Any = None

        # Verificação de certificado sempre ligada; o bundle só acrescenta a CA do MK.
        verificacao = ssl.create_default_context(cafile=ca_bundle) if ca_bundle else True
        self._http = httpx.AsyncClient(
            base_url=str(url).rstrip("/"),
            verify=verificacao,
            timeout=timeout,
            transport=transport,
        )

    @classmethod
    def da_configuracao(cls, config: Configuracao, **opcoes: Any) -> "ClienteMk":
        return cls(
            str(config.mk_base_url) if config.mk_base_url else None,
            config.mk_token_usuario,
            config.mk_contra_senha,
            ca_bundle=config.mk_ca_bundle,
            **opcoes,
        )

    async def __aenter__(self) -> "ClienteMk":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.fechar()

    async def fechar(self) -> None:
        await self._http.aclose()

    async def consultar(
        self,
        servico: str,
        parametros: dict[str, str | int] | None = None,
        *,
        timeout_http: float | None = None,
    ) -> Corpo:
        """Chamada de LEITURA a /mk/<servico>.rule. Devolve o corpo JSON já verificado.

        timeout_http (segundos) substitui o padrão do cliente só nesta chamada. Vale para cada
        requisição HTTP; a espera por vaga no limite de simultâneas não conta.
        Se o MK disser que o token expirou, reautentica e repete UMA vez. Um futuro método de
        escrita não pode fazer isso: o erro sobe, sem repetição automática.
        """
        if not NOME_DE_SERVICO.fullmatch(servico):
            raise ValueError(f"nome de serviço inválido: {servico!r}")
        parametros = dict(parametros or {})
        if reservados := PARAMETROS_RESERVADOS & parametros.keys():
            raise ValueError(f"parâmetros reservados ao cliente: {sorted(reservados)}")

        # Cada _get pega uma vaga do limite de simultâneas e a devolve ao terminar. Por isso
        # a reautenticação abaixo, que também precisa de vaga, nunca espera por uma vaga
        # presa por esta mesma consulta.
        token, geracao = await self._token_valido()
        corpo = await self._get(
            servico, {**parametros, "token": token.get_secret_value()}, timeout_http
        )
        if token_expirado(corpo):
            log.info("MK %s: token expirado; reautenticando e repetindo uma vez", servico)
            token = await self._renovar(geracao_recusada=geracao)
            corpo = await self._get(
                servico, {**parametros, "token": token.get_secret_value()}, timeout_http
            )
        verificar_sucesso(servico, corpo, self._segredos())
        return corpo

    async def _token_valido(self) -> tuple[SecretStr, int]:
        if self._token_em_dia():
            return self._token, self._geracao
        async with self._lock:
            # Quem esperou o lock encontra o token já renovado por outra chamada.
            if not self._token_em_dia():
                await self._autenticar()
            return self._token, self._geracao

    async def _renovar(self, geracao_recusada: int) -> SecretStr:
        async with self._lock:
            if self._geracao == geracao_recusada or not self._token_em_dia():
                await self._autenticar()  # senão, outra chamada já renovou
            return self._token

    def _token_em_dia(self) -> bool:
        return (
            self._token is not None
            and self._expira_em is not None
            and self._relogio() < self._expira_em - MARGEM_RENOVACAO
        )

    async def _autenticar(self) -> None:
        corpo = await self._get(
            SERVICO_AUTENTICACAO,
            {
                "token": self._token_usuario.get_secret_value(),
                "password": self._contra_senha.get_secret_value(),
                "cd_servico": CD_SERVICO_AUTENTICACAO,
            },
        )
        verificar_sucesso(SERVICO_AUTENTICACAO, corpo, self._segredos())
        faltando = [campo for campo in ("Token", "Expire") if not corpo.get(campo)]
        if faltando:
            raise RespostaMkInesperada(f"{SERVICO_AUTENTICACAO}: sem {', '.join(faltando)}")
        try:
            expira_em = datetime.strptime(corpo["Expire"], FORMATO_EXPIRE).replace(tzinfo=FUSO_MK)
        except (TypeError, ValueError):
            raise RespostaMkInesperada(
                f"{SERVICO_AUTENTICACAO}: Expire fora do formato {FORMATO_EXPIRE}"
            ) from None
        self._token = SecretStr(str(corpo["Token"]))
        self._expira_em = expira_em
        self._geracao += 1
        self.limite_uso = corpo.get("LimiteUso")
        self.servicos_autorizados = corpo.get("ServicosAutorizados")
        log.info("MK: autenticado; token válido até %s", expira_em.isoformat())

    async def _get(
        self, servico: str, parametros: dict[str, str | int], timeout_http: float | None = None
    ) -> Corpo:
        """GET em /mk/<servico>.rule. Erros só citam o serviço, nunca a URL."""
        try:
            async with self._vagas:
                resposta = await self._http.get(
                    f"/mk/{servico}.rule",
                    params={"sys": SISTEMA, **parametros},
                    timeout=httpx.USE_CLIENT_DEFAULT if timeout_http is None else timeout_http,
                )
        except httpx.HTTPError as erro:
            raise MkInacessivel(f"{servico}: {type(erro).__name__}") from None
        if resposta.status_code >= 500:
            raise MkInacessivel(f"{servico}: HTTP {resposta.status_code}")
        if resposta.status_code != 200:
            raise RespostaMkInesperada(f"{servico}: HTTP {resposta.status_code}")
        return _decodificar(servico, resposta)

    def _segredos(self) -> list[str]:
        segredos = [self._token_usuario, self._contra_senha, self._token]
        return [s.get_secret_value() for s in segredos if s is not None]


def _decodificar(servico: str, resposta: httpx.Response) -> Corpo:
    """Corpo JSON -> dict. Erro sem o corpo, que pode ser uma página HTML inteira.

    PENDENTE: validar com respostas reais. A documentação do MK mostra textos com acentuação
    quebrada; não tentamos corrigir. Conferir se o MK declara o charset certo e se os textos
    chegam íntegros antes de comparar ou gravar qualquer texto vindo dele.
    """
    try:
        corpo = resposta.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        tipo = resposta.headers.get("content-type", "sem content-type")
        raise RespostaMkInesperada(
            f"{servico}: resposta não é JSON ({tipo}, {len(resposta.content)} bytes)"
        ) from None
    if not isinstance(corpo, dict):
        # PENDENTE: validar com respostas reais se algum serviço devolve lista na raiz.
        raise RespostaMkInesperada(f"{servico}: JSON sem objeto na raiz ({type(corpo).__name__})")
    return corpo


def verificar_sucesso(servico: str, corpo: Corpo, segredos: list[str]) -> None:
    """Único lugar que decide se uma resposta do MK é sucesso.

    O formato de sucesso não é padronizado entre serviços ("OK", "sucesso", boolean). Erro
    explícito vira ErroDoMk; formato não reconhecido vira RespostaMkInesperada (na dúvida,
    não é sucesso).
    PENDENTE: validar com respostas reais o campo e os valores de sucesso de cada serviço.
    """
    status = corpo.get("status")
    if isinstance(status, str) and status.strip().upper() == "ERRO":
        raise ErroDoMk(
            servico,
            _texto_seguro(corpo.get("Num. ERRO"), segredos),
            _texto_seguro(corpo.get("Mensagem"), segredos),
        )
    if status is True:
        return
    if isinstance(status, str) and status.strip().upper() in {"OK", "SUCESSO"}:
        return
    raise RespostaMkInesperada(f"{servico}: status de sucesso não reconhecido")


def token_expirado(corpo: Corpo) -> bool:
    """Diz se o MK recusou a chamada por token expirado.

    PENDENTE: validar com resposta real do MK. Não sabemos o Num. ERRO nem o texto da Mensagem
    para token expirado; até lá, nunca reconhece (e a renovação 2 minutos antes do Expire é o
    que evita o caso). Ajustar só esta função quando houver um exemplo real.
    """
    return False


def _texto_seguro(valor: Any, segredos: list[str], limite: int = 200) -> str | None:
    """Texto vindo do MK para mensagem de erro: sem nenhum segredo conhecido e com tamanho
    limitado (não sabemos se o MK ecoa parâmetros na Mensagem)."""
    if valor is None:
        return None
    texto = str(valor)
    for segredo in segredos:
        if segredo:
            texto = texto.replace(segredo, "***")
    return texto[:limite]
