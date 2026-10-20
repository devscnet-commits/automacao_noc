"""Pool de conexões com o PostgreSQL (psycopg 3, assíncrono).

Uso:
    async with pool_de_conexoes(obter_configuracao()) as pool:
        async with pool.connection() as conn:
            await conn.execute(...)
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from psycopg_pool import AsyncConnectionPool

from app.config import Configuracao

POOL_MIN = 1
POOL_MAX = 5


class PapelDoBancoProibido(RuntimeError):
    """A aplicação tentou operar com um papel superusuário (ex.: noc)."""


@asynccontextmanager
async def pool_de_conexoes(config: Configuracao) -> AsyncIterator[AsyncConnectionPool]:
    """Abre o pool, confirma que o papel conectado não é superusuário e fecha ao sair."""
    async with AsyncConnectionPool(
        config.conninfo_banco(),
        min_size=POOL_MIN,
        max_size=POOL_MAX,
        open=False,
        check=AsyncConnectionPool.check_connection,
        name="noc",
    ) as pool:
        async with pool.connection() as conn:
            cur = await conn.execute("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
            (superusuario,) = await cur.fetchone()
        if superusuario:
            raise PapelDoBancoProibido(
                "a aplicação não pode conectar com um superusuário; use o noc_app"
            )
        yield pool
