"""Configuração da aplicação, lida do ambiente (prefixo NOC_) e, se existir, do arquivo .env.

Todo segredo é SecretStr: aparece como '**********' em repr, log e mensagem de erro.
A aplicação não lê a DATABASE_URL (é do dbmate, com o superusuário noc): conecta como
noc_app, montando a conexão com NOC_DB_* + a senha lida de NOC_DB_PASSWORD_FILE.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from psycopg.conninfo import make_conninfo
from pydantic import BaseModel, ConfigDict, HttpUrl, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

VersaoSnmp = Literal["v2c", "v3"]


class CredenciaisOlt(BaseModel):
    """Credenciais de leitura de uma OLT.

    Tudo opcional: numa sobrescrita (NOC_OLT_SOBRESCRITAS), o que faltar vem do padrão
    global. Validar se a credencial necessária existe é papel de quem vai usá-la.
    """

    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    snmp_versao: VersaoSnmp | None = None
    snmp_community: SecretStr | None = None
    ssh_usuario: str | None = None
    ssh_senha: SecretStr | None = None
    # PENDENTE: validar com NOC/bancada. SNMPv3 NÃO está implementado: os campos só
    # reservam o formato.
    snmp_v3_usuario: str | None = None
    snmp_v3_auth_protocolo: str | None = None
    snmp_v3_auth_senha: SecretStr | None = None
    snmp_v3_priv_protocolo: str | None = None
    snmp_v3_priv_senha: SecretStr | None = None


class Configuracao(BaseSettings):
    # extra="ignore": o .env é compartilhado com o dbmate (DATABASE_URL, DBMATE_*).
    # env_ignore_empty: variável vazia (como no .env.example) conta como não definida.
    model_config = SettingsConfigDict(
        env_prefix="NOC_",
        env_file=".env",
        extra="ignore",
        env_ignore_empty=True,
        hide_input_in_errors=True,
    )

    db_host: str
    db_porta: int = 5432
    db_usuario: str
    db_nome: str
    db_password_file: Path

    mk_base_url: HttpUrl
    mk_token_usuario: SecretStr
    mk_contra_senha: SecretStr

    # Padrão global das OLTs: os mesmos campos de CredenciaisOlt, com prefixo olt_.
    olt_snmp_versao: VersaoSnmp | None = None
    olt_snmp_community: SecretStr | None = None
    olt_ssh_usuario: str | None = None
    olt_ssh_senha: SecretStr | None = None
    # PENDENTE: validar com NOC/bancada. SNMPv3 não implementado.
    olt_snmp_v3_usuario: str | None = None
    olt_snmp_v3_auth_protocolo: str | None = None
    olt_snmp_v3_auth_senha: SecretStr | None = None
    olt_snmp_v3_priv_protocolo: str | None = None
    olt_snmp_v3_priv_senha: SecretStr | None = None
    # JSON indexado pelo nome da OLT (olts.nome).
    olt_sobrescritas: dict[str, CredenciaisOlt] = {}

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    def conninfo_banco(self) -> str:
        """String de conexão do psycopg, com a senha lida do arquivo. Nunca logar."""
        senha = self.db_password_file.read_text().rstrip("\n")
        return make_conninfo(
            host=self.db_host,
            port=self.db_porta,
            user=self.db_usuario,
            dbname=self.db_nome,
            password=senha,
            application_name="automacao_noc",
        )

    def credenciais_olt(self, nome_olt: str) -> CredenciaisOlt:
        """Padrão global com os campos que a sobrescrita da OLT definir por cima."""
        padrao = CredenciaisOlt(
            **{campo: getattr(self, f"olt_{campo}") for campo in CredenciaisOlt.model_fields}
        )
        sobrescrita = self.olt_sobrescritas.get(nome_olt)
        if sobrescrita is None:
            return padrao
        return padrao.model_copy(update=sobrescrita.model_dump(exclude_unset=True))


@lru_cache
def obter_configuracao() -> Configuracao:
    return Configuracao()
