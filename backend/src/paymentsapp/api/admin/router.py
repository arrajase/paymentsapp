"""Admin API (/admin/v1), used only by the admin website. Every route is behind the IP allowlist."""

from typing import Annotated

from fastapi import APIRouter, Depends, Header, Request, status

from paymentsapp.api.deps import CurrentAdmin, DbSession, admin_ip_allowed, client_ip
from paymentsapp.core.config import get_settings
from paymentsapp.modules.admin import service
from paymentsapp.modules.admin.models import AdminUser
from paymentsapp.modules.admin.schemas import AdminOut, SignInOptions, SignInRequest, SignInResponse

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(admin_ip_allowed)], tags=["admin"])


def _admin_out(user: AdminUser) -> AdminOut:
    return AdminOut(id=user.id, login_id=user.login_id, display_name=user.display_name, roles=user.roles)


@router.get("/auth/options", response_model=SignInOptions)
async def sign_in_options() -> SignInOptions:
    """Public: tells the sign-in page whether to ask for an authenticator code."""
    return SignInOptions(totp_required=service.totp_required())


@router.post("/auth/sign-in", response_model=SignInResponse)
async def sign_in(
    body: SignInRequest,
    request: Request,
    session: DbSession,
    user_agent: Annotated[str | None, Header()] = None,
) -> SignInResponse:
    result = await service.sign_in(
        session, body.login_id, body.password, body.totp_code, ip=client_ip(request), user_agent=user_agent
    )
    return SignInResponse(
        token=result.token,
        expires_at=result.session.expires_at,
        idle_timeout_minutes=get_settings().admin_session_idle_minutes,
        admin=_admin_out(result.user),
    )


@router.post("/auth/sign-out", status_code=status.HTTP_204_NO_CONTENT)
async def sign_out(admin: CurrentAdmin, session: DbSession) -> None:
    await service.sign_out(session, admin)


@router.get("/me", response_model=AdminOut)
async def me(admin: CurrentAdmin) -> AdminOut:
    return _admin_out(admin.user)
