"""App factory. Run locally with: uv run uvicorn paymentsapp.main:app --port 8001 --reload"""

from fastapi import FastAPI

from paymentsapp.api.admin.plugins import router as admin_plugins_router
from paymentsapp.api.admin.router import router as admin_router
from paymentsapp.api.middleware import register_middleware
from paymentsapp.core.config import Environment, get_settings
from paymentsapp.core.errors import register_error_handlers
from paymentsapp.core.logging import configure_logging


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Interactive API docs are a development convenience; they map the whole attack surface in production.
    docs_enabled = settings.environment != Environment.PRODUCTION
    app = FastAPI(
        title="paymentsapp",
        docs_url="/docs" if docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if docs_enabled else None,
    )
    register_middleware(app)
    register_error_handlers(app)

    app.include_router(admin_router)
    app.include_router(admin_plugins_router)

    @app.get("/health", include_in_schema=False)
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
