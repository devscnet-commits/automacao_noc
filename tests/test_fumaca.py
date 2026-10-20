"""Teste de fumaça: o pacote importa e a configuração carrega. Os testes de verdade vêm depois."""

import importlib
import pkgutil

import pytest

import app
from app.config import Configuracao


def test_todos_os_modulos_de_app_importam():
    nomes = [m.name for m in pkgutil.walk_packages(app.__path__, prefix="app.")]
    assert nomes
    for nome in nomes:
        importlib.import_module(nome)


def test_configuracao_carrega_sem_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NOC_DB_HOST", "127.0.0.1")
    monkeypatch.setenv("NOC_DB_USUARIO", "noc_app")
    monkeypatch.setenv("NOC_DB_NOME", "noc")
    monkeypatch.setenv("NOC_DB_PASSWORD_FILE", "/nao/existe")

    config = Configuracao(_env_file=None)

    assert config.db_usuario == "noc_app"
    assert config.mk_base_url is None
