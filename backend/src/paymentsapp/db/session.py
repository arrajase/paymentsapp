"""Async database engine and sessions (PostgreSQL via asyncpg).

DATABASE_URL can be pasted exactly as Neon shows it (postgresql://...?sslmode=require&channel_binding=require).
asyncpg doesn't understand libpq's sslmode or channel_binding parameters, so they are removed from the URL and TLS
is configured here instead, always with certificate and hostname verification (plan 9.5: TLS verification on).
"""

import ssl
from collections.abc import AsyncIterator

from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from paymentsapp.core.config import get_settings

_LIBPQ_ONLY_PARAMS = ("sslmode", "channel_binding", "sslrootcert", "sslcert", "sslkey")
_LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}


def to_async_url(raw: str) -> tuple[URL, bool]:
    """Return the asyncpg URL and whether TLS is required. TLS is required for every non-local host."""
    url = make_url(raw)
    sslmode = url.query.get("sslmode")
    if isinstance(sslmode, tuple):
        sslmode = sslmode[-1]
    url = url.set(drivername="postgresql+asyncpg").difference_update_query(_LIBPQ_ONLY_PARAMS)
    needs_tls = url.host not in _LOCAL_HOSTS or sslmode in {"require", "verify-ca", "verify-full"}
    return url, needs_tls


def create_engine(raw_url: str, *, echo: bool = False) -> AsyncEngine:
    url, needs_tls = to_async_url(raw_url)
    connect_args = {"ssl": ssl.create_default_context()} if needs_tls else {}
    return create_async_engine(
        url,
        echo=echo,
        connect_args=connect_args,
        pool_pre_ping=True,  # Neon suspends idle computes; drop dead connections instead of failing a request
        pool_size=5,
        max_overflow=5,
    )


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        database_url = get_settings().database_url
        if database_url is None:
            raise RuntimeError("DATABASE_URL is not set")
        _engine = create_engine(database_url.get_secret_value())
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. Callers commit explicitly; anything uncommitted rolls back."""
    get_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session
