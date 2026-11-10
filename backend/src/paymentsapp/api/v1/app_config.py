"""Customer API: GET /v1/app-config, read by the mobile app at startup and sign-in (plan 3.2).

Public, because the app needs it before anyone signs in (for example, whether to pin certificates). It contains
only settings marked `client: true` and feature switches, never anything secret. The server never relies on the
app having obeyed it.
"""

from datetime import UTC, datetime

from fastapi import APIRouter, Response

from paymentsapp.api.deps import DbSession
from paymentsapp.core.config import get_settings
from paymentsapp.modules.controls.schemas import AppConfigOut
from paymentsapp.modules.controls.service import effective_values

router = APIRouter(prefix="/v1", tags=["customer"])


@router.get("/app-config", response_model=AppConfigOut)
async def app_config(session: DbSession, response: Response) -> AppConfigOut:
    response.headers["Cache-Control"] = "no-store"
    return AppConfigOut(
        environment=get_settings().environment.value,
        controls=await effective_values(session, client_only=True),
        generated_at=datetime.now(UTC),
    )
