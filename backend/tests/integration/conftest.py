"""Integration tests run against TEST_DATABASE_URL (a disposable Neon database whose name ends in _test).

Once per run the schema is wiped and rebuilt with the real Alembic migrations, so tests exercise exactly what
production runs. Every test then works inside a transaction that is rolled back, so tests never see each other's
data. Without TEST_DATABASE_URL every integration test is skipped.
"""

import asyncio
from collections.abc import AsyncIterator

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from paymentsapp.core.config import BACKEND_DIR, Settings
from paymentsapp.db.session import create_engine


def _test_database_url() -> str:
    configured = Settings().test_database_url
    if configured is None:
        pytest.skip("integration tests need TEST_DATABASE_URL in backend/.env")
    url = configured.get_secret_value()
    database = make_url(url).database or ""
    if not database.endswith("_test"):
        pytest.exit(f"refusing to wipe database {database!r}: TEST_DATABASE_URL must name a *_test database")
    return url


async def _wipe(url: str) -> None:
    engine = create_engine(url)
    async with engine.begin() as conn:
        await conn.execute(text("DROP SCHEMA IF EXISTS public CASCADE"))
        await conn.execute(text("CREATE SCHEMA public"))
    await engine.dispose()


@pytest.fixture(scope="session")
def database_url() -> str:
    url = _test_database_url()
    asyncio.run(_wipe(url))
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["database_url"] = url
    config.attributes["configure_logging"] = False
    command.upgrade(config, "head")
    return url


@pytest.fixture(scope="session")
async def engine(database_url: str) -> AsyncIterator[AsyncEngine]:
    """One pool for the whole run: each new connection to Neon costs a TLS handshake over the network."""
    shared = create_engine(database_url)
    yield shared
    await shared.dispose()


@pytest.fixture
async def session(engine: AsyncEngine) -> AsyncIterator[AsyncSession]:
    """A session inside an outer transaction that is always rolled back. session.commit() only ends a savepoint."""
    async with engine.connect() as connection:
        outer = await connection.begin()
        db = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield db
        finally:
            await db.close()
            await outer.rollback()
