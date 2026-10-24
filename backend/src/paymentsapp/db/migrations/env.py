"""Alembic environment. Uses DATABASE_URL (or the URL passed by the test suite) and the app's TLS settings."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy import Connection

from paymentsapp.core.config import get_settings
from paymentsapp.db import models  # noqa: F401  (imports every module's tables onto Base.metadata)
from paymentsapp.db.base import Base
from paymentsapp.db.session import create_engine

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logging", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    # The test suite passes its own URL through attributes, so tests never touch DATABASE_URL.
    url: str | None = config.attributes.get("database_url")
    if url:
        return url
    configured = get_settings().database_url
    if configured is None:
        raise RuntimeError("DATABASE_URL is not set in backend/.env")
    return configured.get_secret_value()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        context.run_migrations()


async def _run_async() -> None:
    engine = create_engine(_database_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run)
    await engine.dispose()


if context.is_offline_mode():
    raise SystemExit("offline (--sql) migrations are not supported; run against a database")

asyncio.run(_run_async())
