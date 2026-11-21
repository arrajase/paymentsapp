"""App factory. Run locally with: uv run uvicorn paymentsapp.main:app --port 8001 --reload"""

import asyncio
import contextlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from paymentsapp.api.admin.controls import router as admin_controls_router
from paymentsapp.api.admin.dev import router as admin_dev_router
from paymentsapp.api.admin.finance import router as admin_finance_router
from paymentsapp.api.admin.plugins import router as admin_plugins_router
from paymentsapp.api.admin.router import router as admin_router
from paymentsapp.api.middleware import register_middleware
from paymentsapp.api.v1.app_config import router as app_config_router
from paymentsapp.api.v1.webhooks import router as webhooks_router
from paymentsapp.core.config import Environment, get_settings
from paymentsapp.core.errors import register_error_handlers
from paymentsapp.core.logging import configure_logging
from paymentsapp.integrations.registry import close_clients
from paymentsapp.modules.consent.router import router as consent_router
from paymentsapp.modules.identity.router import router as identity_router
from paymentsapp.modules.kyc.router import router as kyc_router
from paymentsapp.modules.upi.router import router as upi_router
from paymentsapp.modules.wallet.router import router as wallet_router


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    worker: asyncio.Task[None] | None = None
    if get_settings().run_worker_in_api:
        from paymentsapp import worker as background

        worker = asyncio.create_task(background.run())
    yield
    if worker is not None:
        worker.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await worker
    await close_clients()


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Interactive API docs are a development convenience; they map the whole attack surface in production.
    docs_enabled = (
        settings.api_docs_enabled
        if settings.api_docs_enabled is not None
        else settings.environment != Environment.PRODUCTION
    )
    app = FastAPI(
        title="paymentsapp",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
        lifespan=lifespan,
    )
    register_middleware(app)
    register_error_handlers(app)

    app.include_router(admin_router)
    app.include_router(admin_plugins_router)
    app.include_router(admin_controls_router)
    app.include_router(admin_dev_router)
    app.include_router(admin_finance_router)
    app.include_router(app_config_router)
    app.include_router(identity_router)
    app.include_router(kyc_router)
    app.include_router(consent_router)
    app.include_router(upi_router)
    app.include_router(wallet_router)
    app.include_router(webhooks_router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
