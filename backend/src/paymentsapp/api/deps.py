"""Request dependencies: database session, admin authentication, role checks and the admin IP allowlist."""

import ipaddress
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Request
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Environment, get_settings
from paymentsapp.core.errors import AppError
from paymentsapp.db.session import get_session
from paymentsapp.modules.admin.service import AdminContext, AdminRole, SessionInvalid, resolve_session

DbSession = Annotated[AsyncSession, Depends(get_session)]


def client_ip(request: Request) -> str | None:
    # Direct peer address. Behind the production gateway this must come from its trusted forwarding header instead.
    return request.client.host if request.client else None


async def admin_ip_allowed(request: Request) -> None:
    """Router-level guard for /admin (plan M16): only allowlisted networks, and none at all in production by default."""
    settings = get_settings()
    networks = [ipaddress.ip_network(cidr, strict=False) for cidr in settings.admin_ip_allowlist]
    if not networks:
        if settings.environment == Environment.PRODUCTION:
            raise AppError("FORBIDDEN", "not allowed from this network", status_code=403)
        return
    ip = client_ip(request)
    try:
        address = ipaddress.ip_address(ip or "")
    except ValueError:
        raise AppError("FORBIDDEN", "not allowed from this network", status_code=403) from None
    if not any(address in network for network in networks):
        raise AppError("FORBIDDEN", "not allowed from this network", status_code=403)


async def current_admin(
    request: Request,
    session: DbSession,
    authorization: Annotated[str | None, Header()] = None,
) -> AdminContext:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise SessionInvalid()
    return await resolve_session(session, token.strip(), ip=client_ip(request))


CurrentAdmin = Annotated[AdminContext, Depends(current_admin)]


def require_roles(*roles: AdminRole) -> Callable[..., Awaitable[AdminContext]]:
    """Dependency allowing only admins holding at least one of `roles`."""
    wanted = set(roles)

    async def check(admin: CurrentAdmin) -> AdminContext:
        if not admin.has_any(wanted):
            raise AppError("FORBIDDEN", "your role does not allow this", status_code=403)
        return admin

    return check
