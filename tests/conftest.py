import os

import pytest


@pytest.fixture(autouse=True)
def sem_variaveis_noc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cada teste começa sem nenhuma variável NOC_ da máquina (nem do CI)."""
    for nome in list(os.environ):
        if nome.upper().startswith("NOC_"):
            monkeypatch.delenv(nome)
