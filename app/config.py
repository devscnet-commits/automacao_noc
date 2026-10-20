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
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict

PREFIXO = "NOC_"

VersaoSnmp = Literal["v2c", "v3"]


class VariavelDesconhecida(ValueError):
    """Variável com prefixo NOC_ que não corresponde a nenhum campo (erro de digitação)."""


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
    # extra="ignore": o .env é compartilhado com o dbmate (DATABASE_URL, DBMATE_*). Variáveis
    # com prefixo NOC_ desconhecidas são recusadas em settings_customise_sources.
    # env_ignore_empty: variável vazia (como no .env.example) conta como não definida.
    model_config = SettingsConfigDict(
        env_prefix=PREFIXO,
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

    # Opcionais na carga: a coleta das OLTs roda sem o MK. O cliente MK valida ao usar.
    mk_base_url: HttpUrl | None = None
    mk_token_usuario: SecretStr | None = None
    mk_contra_senha: SecretStr | None = None

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

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        """Recusa variável NOC_ desconhecida, do ambiente ou do .env. Só cita o nome."""
        conhecidas = {(PREFIXO + campo).lower() for campo in cls.model_fields}
        lidas = {*env_settings.env_vars, *dotenv_settings.env_vars}  # chaves em minúsculas
        desconhecidas = sorted(
            nome.upper()
            for nome in lidas
            if nome.startswith(PREFIXO.lower()) and nome not in conhecidas
        )
        if desconhecidas:
            raise VariavelDesconhecida(
                "variáveis com prefixo NOC_ não reconhecidas: " + ", ".join(desconhecidas)
            )
        return init_settings, env_settings, dotenv_settings, file_secret_settings

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
        # PENDENTE: validar com NOC/bancada. Ao implementar o SNMPv3, mudar esta mesclagem:
        # hoje uma OLT com snmp_versao "v3" na sobrescrita herda a snmp_community (v2c) do
        # padrão global, e os campos snmp_v3_* globais vazam para OLTs v2c. A mesclagem tem
        # de considerar a versão efetiva e só herdar os campos daquela versão.
        return padrao.model_copy(update=sobrescrita.model_dump(exclude_unset=True))


@lru_cache
def obter_configuracao() -> Configuracao:
    return Configuracao()
