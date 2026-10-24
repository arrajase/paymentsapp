"""DATABASE_URL pasted straight from Neon must become a TLS-verified asyncpg URL."""

from paymentsapp.db.session import to_async_url

NEON = "postgresql://role:pw@ep-x.region.aws.neon.tech/paymentsapp?sslmode=require&channel_binding=require"


def test_neon_url_becomes_asyncpg_without_libpq_params() -> None:
    url, needs_tls = to_async_url(NEON)
    assert url.drivername == "postgresql+asyncpg"
    assert "sslmode" not in url.query and "channel_binding" not in url.query
    assert url.database == "paymentsapp"
    assert url.password == "pw"
    assert needs_tls


def test_psycopg_style_url_is_accepted_too() -> None:
    url, _ = to_async_url(NEON.replace("postgresql://", "postgresql+psycopg://"))
    assert url.drivername == "postgresql+asyncpg"


def test_remote_host_needs_tls_even_without_sslmode() -> None:
    _, needs_tls = to_async_url("postgresql://u:p@db.example.com/app")
    assert needs_tls


def test_local_host_without_sslmode_skips_tls() -> None:
    _, needs_tls = to_async_url("postgresql://u:p@localhost:5432/app")
    assert not needs_tls
