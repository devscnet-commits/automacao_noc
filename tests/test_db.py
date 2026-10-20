"""Pool do banco contra um PostgreSQL de verdade, com as migrações aplicadas.

Roda só com TESTE_PG_HOST definido (no CI, o serviço PostgreSQL). Variáveis:
TESTE_PG_HOST, TESTE_PG_PORTA, TESTE_PG_NOME, TESTE_PG_SUPERUSUARIO,
TESTE_PG_SUPERUSUARIO_SENHA e TESTE_PG_APP_SENHA (senha já definida para o noc_app).
Nada é gravado: tudo o que o teste insere é desfeito com rollback.
"""

import os

import psycopg
import pytest

from app.config import Configuracao
from app.db import PapelDoBancoProibido, pool_de_conexoes

pytestmark = [
    pytest.mark.banco,
    # No CI (CI=true) nunca pula: se a variável faltar, o teste falha em vez de sumir.
    pytest.mark.skipif(
        not os.environ.get("TESTE_PG_HOST") and not os.environ.get("CI"),
        reason="TESTE_PG_HOST não definido",
    ),
]


def configuracao(tmp_path, usuario: str, senha: str) -> Configuracao:
    arquivo = tmp_path / f"senha-{usuario}"
    arquivo.write_text(senha + "\n")
    return Configuracao(
        _env_file=None,
        db_host=os.environ["TESTE_PG_HOST"],
        db_porta=int(os.environ.get("TESTE_PG_PORTA", "5432")),
        db_nome=os.environ.get("TESTE_PG_NOME", "noc"),
        db_usuario=usuario,
        db_password_file=arquivo,
    )


async def test_superusuario_e_recusado(tmp_path):
    config = configuracao(
        tmp_path,
        os.environ.get("TESTE_PG_SUPERUSUARIO", "noc"),
        os.environ["TESTE_PG_SUPERUSUARIO_SENHA"],
    )
    with pytest.raises(PapelDoBancoProibido):
        async with pool_de_conexoes(config):
            pass


async def test_noc_app_conecta(tmp_path):
    config = configuracao(tmp_path, "noc_app", os.environ["TESTE_PG_APP_SENHA"])
    async with pool_de_conexoes(config) as pool, pool.connection() as conn:
        cur = await conn.execute("SELECT current_user")
        assert (await cur.fetchone())[0] == "noc_app"


async def test_privilegios_do_noc_app(tmp_path):
    config = configuracao(tmp_path, "noc_app", os.environ["TESTE_PG_APP_SENHA"])
    async with pool_de_conexoes(config) as pool, pool.connection() as conn:
        try:
            cur = await conn.execute(
                "INSERT INTO olts (nome, fabricante, endereco_ip) "
                "VALUES ('OLT-TESTE-PYTEST', 'huawei', '192.0.2.250') RETURNING id"
            )
            (olt_id,) = await cur.fetchone()
            cur = await conn.execute(
                "INSERT INTO pons (olt_id, chassi, slot, porta) VALUES (%s, 0, 1, 0) RETURNING id",
                [olt_id],
            )
            (pon_id,) = await cur.fetchone()
            cur = await conn.execute(
                "INSERT INTO onus (pon_id, onu_id) VALUES (%s, 0) "
                "ON CONFLICT (pon_id, onu_id) DO UPDATE SET serial = 'X' RETURNING id",
                [pon_id],
            )
            (onu_id,) = await cur.fetchone()
            await conn.execute(
                "INSERT INTO leituras_sinal (onu_id, tipo, rx_onu_dbm) "
                "VALUES (%s, 'ativacao', -19.5)",
                [onu_id],
            )
            await conn.execute("UPDATE onus SET serial = 'Y' WHERE id = %s", [onu_id])

            negados = [
                "UPDATE leituras_sinal SET rx_onu_dbm = 0",
                "DELETE FROM leituras_sinal",
                "DELETE FROM onus",
                "TRUNCATE olts CASCADE",
                "CREATE TABLE intrusa (id int)",
            ]
            for sql in negados:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    async with conn.transaction():  # savepoint: o erro não perde o resto
                        await conn.execute(sql)
        finally:
            await conn.rollback()
