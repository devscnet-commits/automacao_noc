"""Configuração: segredos fora do repr e das mensagens, sobrescrita por OLT, variável NOC_
desconhecida recusada. (A recusa de superusuário está em test_db.py: precisa do banco.)"""

import pytest
from pydantic import ValidationError

from app.config import Configuracao, VariavelDesconhecida
from app.mk.cliente import ClienteMk, ConfiguracaoMkInvalida

SEGREDOS = {
    "NOC_MK_TOKEN_USUARIO": "segredo-token-usuario",
    "NOC_MK_CONTRA_SENHA": "segredo-contra-senha",
    "NOC_OLT_SNMP_COMMUNITY": "segredo-community-global",
    "NOC_OLT_SSH_SENHA": "segredo-ssh",
}


@pytest.fixture
def banco(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    senha = tmp_path / "senha"
    senha.write_text("segredo-banco\n")
    monkeypatch.setenv("NOC_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("NOC_DB_USUARIO", "noc_app")
    monkeypatch.setenv("NOC_DB_NOME", "noc")
    monkeypatch.setenv("NOC_DB_PASSWORD_FILE", str(senha))


def carregar() -> Configuracao:
    return Configuracao(_env_file=None)


def sem_segredo(texto: str) -> bool:
    return not any(valor in texto for valor in [*SEGREDOS.values(), "segredo-banco"])


# --- segredos fora do repr e das mensagens ----------------------------------------------


def test_repr_nao_mostra_segredos(banco, monkeypatch):
    for nome, valor in SEGREDOS.items():
        monkeypatch.setenv(nome, valor)
    monkeypatch.setenv(
        "NOC_OLT_SOBRESCRITAS", '{"OLT-A": {"snmp_community": "segredo-community-a"}}'
    )
    config = carregar()

    texto = repr(config) + str(config) + repr(config.credenciais_olt("OLT-A"))
    assert sem_segredo(texto)
    assert "segredo-community-a" not in texto
    assert config.credenciais_olt("OLT-A").snmp_community.get_secret_value() == (
        "segredo-community-a"
    )


def test_conninfo_le_a_senha_do_arquivo(banco):
    config = carregar()
    assert "password=segredo-banco" in config.conninfo_banco()
    assert "user=noc_app" in config.conninfo_banco()


@pytest.mark.parametrize(
    "sobrescritas",
    [
        '{"OLT-A": {"snmp_comunity": "segredo-community-global"}}',  # campo com erro de digitação
        '{"OLT-A": {"snmp_versao": "segredo-community-global"}}',  # valor inválido
        '{"OLT-A": {"snmp_community": "segredo-community-global"',  # JSON quebrado
    ],
    ids=["campo-desconhecido", "valor-invalido", "json-quebrado"],
)
def test_erro_de_configuracao_nao_mostra_o_valor(banco, monkeypatch, sobrescritas):
    monkeypatch.setenv("NOC_OLT_SOBRESCRITAS", sobrescritas)
    with pytest.raises(Exception) as erro:  # ValidationError ou SettingsError
        carregar()
    assert sem_segredo(str(erro.value))


def test_mk_incompleto_cita_variaveis_e_nao_valores(banco, monkeypatch):
    monkeypatch.setenv("NOC_MK_TOKEN_USUARIO", SEGREDOS["NOC_MK_TOKEN_USUARIO"])
    with pytest.raises(ConfiguracaoMkInvalida) as erro:
        ClienteMk.da_configuracao(carregar())
    assert "NOC_MK_BASE_URL" in str(erro.value)
    assert "NOC_MK_CONTRA_SENHA" in str(erro.value)
    assert sem_segredo(str(erro.value))


def test_campos_obrigatorios_ausentes():
    with pytest.raises(ValidationError) as erro:
        carregar()
    faltando = {e["loc"][0] for e in erro.value.errors()}
    assert faltando == {"db_host", "db_usuario", "db_nome", "db_password_file"}


# --- sobrescrita por OLT -----------------------------------------------------------------


def test_sobrescrita_por_olt(banco, monkeypatch):
    monkeypatch.setenv("NOC_OLT_SNMP_VERSAO", "v2c")
    monkeypatch.setenv("NOC_OLT_SNMP_COMMUNITY", "comm-global")
    monkeypatch.setenv("NOC_OLT_SSH_USUARIO", "leitura")
    monkeypatch.setenv(
        "NOC_OLT_SOBRESCRITAS",
        '{"OLT-A": {"snmp_community": "comm-a"}, "OLT-B": {"ssh_usuario": "outro"}}',
    )
    config = carregar()

    a = config.credenciais_olt("OLT-A")  # campo próprio
    assert a.snmp_community.get_secret_value() == "comm-a"
    assert (a.snmp_versao, a.ssh_usuario) == ("v2c", "leitura")  # o resto vem do global

    b = config.credenciais_olt("OLT-B")  # outro campo próprio; community herdada
    assert b.ssh_usuario == "outro"
    assert b.snmp_community.get_secret_value() == "comm-global"

    sem = config.credenciais_olt("OLT-SEM-SOBRESCRITA")  # só o global
    assert sem.snmp_community.get_secret_value() == "comm-global"


# --- variáveis desconhecidas ----------------------------------------------------------


def test_variavel_noc_desconhecida_no_ambiente(banco, monkeypatch):
    monkeypatch.setenv("NOC_OLT_SOBRESCRITA", '{"OLT-A": {}}')  # falta o S final
    monkeypatch.setenv("NOC_MK_TOKEN_USUAIRO", SEGREDOS["NOC_MK_TOKEN_USUARIO"])
    with pytest.raises(VariavelDesconhecida) as erro:
        carregar()
    assert "NOC_OLT_SOBRESCRITA" in str(erro.value)
    assert "NOC_MK_TOKEN_USUAIRO" in str(erro.value)
    assert sem_segredo(str(erro.value))


def test_variavel_noc_desconhecida_no_arquivo_env(banco, tmp_path):
    arquivo = tmp_path / ".env"
    arquivo.write_text("NOC_LOG_LEVL=DEBUG\n")
    with pytest.raises(VariavelDesconhecida, match="NOC_LOG_LEVL"):
        Configuracao(_env_file=arquivo)


def test_variaveis_de_outros_prefixos_sao_ignoradas(banco, tmp_path, monkeypatch):
    arquivo = tmp_path / ".env"
    arquivo.write_text("DATABASE_URL=postgres://noc:x@127.0.0.1/noc\nDBMATE_NO_DUMP_SCHEMA=true\n")
    monkeypatch.setenv("DBMATE_MIGRATIONS_DIR", "db/migrations")
    assert Configuracao(_env_file=arquivo).db_usuario == "noc_app"


def test_variavel_vazia_conta_como_ausente(banco, monkeypatch):
    monkeypatch.setenv("NOC_MK_TOKEN_USUARIO", "")
    assert carregar().mk_token_usuario is None
