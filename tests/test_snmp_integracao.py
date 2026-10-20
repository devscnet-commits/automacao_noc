"""Walk SNMP contra um agente de verdade (snmpsim), por UDP.

Roda só com TESTE_SNMPSIM_BIN apontando para o snmpsim-command-responder (no CI, instalado num
venv separado). Os OIDs são fictícios (1.3.6.1.4.1.99999), nada de Huawei/Datacom.
"""

import asyncio
import grp
import os
import pwd
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path

import pytest
from pydantic import SecretStr

from app.config import CredenciaisOlt
from app.olt.base import OltInacessivel
from app.olt.snmp import ParametrosSnmp, walk_tabela

pytestmark = [
    pytest.mark.snmpsim,
    # No CI (CI=true) nunca pula: se a variável faltar, o teste falha em vez de sumir.
    pytest.mark.skipif(
        not os.environ.get("TESTE_SNMPSIM_BIN") and not os.environ.get("CI"),
        reason="TESTE_SNMPSIM_BIN não definido",
    ),
]

COMMUNITY = "comunidadeteste"
BASE = "1.3.6.1.4.1.99999"
LINHAS = 48
CRED = CredenciaisOlt(snmp_versao="v2c", snmp_community=SecretStr(COMMUNITY))
RAPIDO = ParametrosSnmp(timeout=1, tentativas=0)


def gerar_dados() -> str:
    linhas = []
    for n in range(LINHAS):
        i = f"4194304000.{n}"
        linhas += [
            (f"{BASE}.1.1.{i}", "2", str(-1950 - n)),  # Integer
            (f"{BASE}.1.2.{i}", "4", f"CX{n:02d}-P{n % 16}"),  # OctetString texto
            (f"{BASE}.1.3.{i}", "4x", f"48575443{n:08x}"),  # OctetString binário
            (f"{BASE}.1.4.{i}", "66", str(1000 + n)),  # Gauge32
            (f"{BASE}.1.5.{i}", "70", str(2**40 + n)),  # Counter64
        ]
    linhas += [(f"{BASE}.1.6.1", "6", "1.3.6.1.2.1"), (f"{BASE}.1.7.1", "64", "192.0.2.9")]
    linhas += [(f"{BASE}.2.1.{n}", "2", "777") for n in range(5)]  # outra tabela, logo depois
    linhas.sort(key=lambda linha: tuple(int(x) for x in linha[0].split(".")))
    return "".join(f"{oid}|{tipo}|{valor}\n" for oid, tipo, valor in linhas)


def porta_udp_livre() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def agente():
    """Sobe o snmpsim numa porta livre e devolve a porta. Como root, o snmpsim exige baixar
    o privilégio: roda como nobody, num diretório que ele consegue ler."""
    diretorio = Path(tempfile.mkdtemp(prefix="snmpsim-"))
    dados, cache = diretorio / "dados", diretorio / "cache"
    dados.mkdir()
    cache.mkdir()
    (dados / f"{COMMUNITY}.snmprec").write_text(gerar_dados())
    porta = porta_udp_livre()
    comando = [
        os.environ["TESTE_SNMPSIM_BIN"],
        f"--data-dir={dados}",
        f"--cache-dir={cache}",
        f"--agent-udpv4-endpoint=127.0.0.1:{porta}",
    ]
    if os.geteuid() == 0:
        usuario = pwd.getpwnam("nobody")
        comando += [
            "--process-user=nobody",
            f"--process-group={grp.getgrgid(usuario.pw_gid).gr_name}",
        ]
        for caminho in (diretorio, dados, cache):
            caminho.chmod(0o777)
        (dados / f"{COMMUNITY}.snmprec").chmod(0o644)

    # Log em arquivo, nunca em PIPE: o snmpsim escreve a cada requisição e, com o PIPE cheio,
    # trava escrevendo e para de responder.
    log = diretorio / "agente.log"
    with log.open("wb") as saida:
        processo = subprocess.Popen(comando, stdout=saida, stderr=subprocess.STDOUT)  # noqa: S603
    try:
        if not asyncio.run(agente_respondeu(porta, processo)):
            raise RuntimeError(f"snmpsim não respondeu: {log.read_text(errors='replace')[-500:]}")
        yield porta
    finally:
        processo.terminate()
        processo.wait(timeout=10)
        shutil.rmtree(diretorio, ignore_errors=True)


async def agente_respondeu(porta: int, processo: subprocess.Popen) -> bool:
    for _ in range(30):
        if processo.poll() is not None:
            return False
        try:
            await walk_tabela("127.0.0.1", CRED, f"{BASE}.1.6", porta=porta, parametros=RAPIDO)
            return True
        except OltInacessivel:
            await asyncio.sleep(0.5)
    return False


async def test_coluna_com_varios_getbulk(agente):
    # 48 linhas com max_repeticoes=10 (padrão): pelo menos 5 requisições GETBULK.
    resultado = await walk_tabela("127.0.0.1", CRED, f"{BASE}.1.1", porta=agente)
    assert len(resultado) == LINHAS
    assert resultado["4194304000.0"] == -1950
    assert resultado["4194304000.47"] == -1997


async def test_tabela_inteira_para_na_fronteira(agente):
    resultado = await walk_tabela("127.0.0.1", CRED, f"{BASE}.1", porta=agente)
    assert len(resultado) == LINHAS * 5 + 2  # nenhuma linha da tabela .2 logo depois
    assert resultado["2.4194304000.7"] == b"CX07-P7"
    assert resultado["3.4194304000.7"] == b"HWTC\x00\x00\x00\x07"
    assert resultado["4.4194304000.7"] == 1007
    assert resultado["5.4194304000.7"] == 2**40 + 7
    assert resultado["6.1"] == "1.3.6.1.2.1"
    assert resultado["7.1"] == b"\xc0\x00\x02\t"


async def test_oid_inexistente(agente):
    assert await walk_tabela("127.0.0.1", CRED, f"{BASE}.9", porta=agente) == {}


async def test_community_errada_vira_oltinacessivel(agente):
    errada = CredenciaisOlt(snmp_versao="v2c", snmp_community=SecretStr("errada"))
    with pytest.raises(OltInacessivel) as erro:
        await walk_tabela("127.0.0.1", errada, f"{BASE}.1", porta=agente, parametros=RAPIDO)
    assert "errada" not in str(erro.value)
