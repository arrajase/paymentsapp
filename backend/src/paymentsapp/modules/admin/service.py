"""Admin accounts, sign-in and sessions (plan M16).

Development sign-in is login ID + password + TOTP code. Production replaces this with SSO and MFA; the session and
role checks below stay the same.
"""

import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Environment, get_settings
from paymentsapp.core.crypto import get_cipher
from paymentsapp.core.errors import AppError
from paymentsapp.core.security import (
    hash_password,
    hash_token,
    new_token,
    new_totp_secret,
    password_needs_rehash,
    password_problem,
    verify_password,
    verify_totp,
)
from paymentsapp.modules.admin.models import AdminSession, AdminUser
from paymentsapp.modules.audit.service import SYSTEM, Actor, record

TOKEN_PREFIX = "adm"  # noqa: S105 - a public token prefix, not a secret


class AdminRole(StrEnum):
    VIEWER = "viewer"
    PLATFORM_ADMIN = "platform_admin"  # proposes plugin and control changes (maker)
    SECURITY_APPROVER = "security_approver"  # approves or rejects them (checker)
    OPERATIONS = "operations"
    COMPLIANCE = "compliance"
    SUPPORT = "support"


class InvalidCredentials(AppError):
    def __init__(self) -> None:
        # One message for every cause, so a failed sign-in never reveals which login IDs exist or which part was wrong.
        super().__init__(
            "INVALID_CREDENTIALS",
            "login ID, password or code is incorrect, or the account is temporarily locked",
            status_code=401,
        )


class SessionInvalid(AppError):
    def __init__(self) -> None:
        super().__init__("UNAUTHORIZED", "sign in again", status_code=401)


def _now() -> datetime:
    return datetime.now(UTC)


def _totp_context(admin_id: uuid.UUID) -> str:
    return f"admin_users.totp_secret:{admin_id}"


# Plain names ("ops.lead") or email addresses ("ops.lead@paymentsapp.com").
LOGIN_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9._@-]{2,63}$")


def totp_required() -> bool:
    """The code is optional only when development explicitly turns it off (ADMIN_TOTP_REQUIRED=false)."""
    settings = get_settings()
    return settings.admin_totp_required or settings.environment != Environment.DEVELOPMENT


def normalise_login_id(login_id: str) -> str:
    return login_id.strip().lower()


@dataclass(frozen=True)
class NewAdmin:
    user: AdminUser
    totp_secret: str  # shown once to set up the authenticator app, never stored in plain text


async def create_admin(
    session: AsyncSession,
    login_id: str,
    display_name: str,
    password: str,
    roles: set[AdminRole],
    actor: Actor = SYSTEM,
) -> NewAdmin:
    problem = password_problem(password)
    if problem:
        raise ValueError(f"password {problem}")
    if not roles:
        raise ValueError("an admin needs at least one role")
    login_id = normalise_login_id(login_id)
    if not LOGIN_ID_PATTERN.match(login_id):
        raise ValueError("login ID must be 3-64 characters: letters, digits, '.', '_', '-' or '@'")
    if await session.scalar(select(AdminUser.id).where(AdminUser.login_id == login_id)):
        raise ValueError("an admin with that login ID already exists")

    admin_id = uuid.uuid4()
    secret = new_totp_secret()
    user = AdminUser(
        id=admin_id,
        login_id=login_id,
        display_name=display_name.strip(),
        password_hash=hash_password(password),
        totp_secret_encrypted=get_cipher().encrypt(secret, _totp_context(admin_id)),
        roles=sorted(r.value for r in roles),
        status="active",
        failed_logins=0,
    )
    session.add(user)
    await session.flush()
    await record(session, actor, "admin.created", "admin_user", str(admin_id), after={"roles": user.roles})
    return NewAdmin(user=user, totp_secret=secret)


@dataclass(frozen=True)
class SignIn:
    token: str
    session: AdminSession
    user: AdminUser


async def sign_in(
    session: AsyncSession,
    login_id: str,
    password: str,
    totp_code: str | None,
    *,
    ip: str | None = None,
    user_agent: str | None = None,
) -> SignIn:
    """Check the credentials and start a session. Commits, so failed attempts are counted even on error.

    The authenticator code is checked unless development has switched it off (see totp_required).
    """
    settings = get_settings()
    now = _now()
    user = await session.scalar(
        select(AdminUser).where(AdminUser.login_id == normalise_login_id(login_id)).with_for_update()
    )

    # Always verify a password, even for unknown or locked accounts, so timing reveals nothing.
    password_ok = verify_password(user.password_hash if user else None, password)
    locked = user is not None and user.locked_until is not None and user.locked_until > now
    need_code = totp_required()
    step: int | None = None
    passed = False
    if user is not None and password_ok and not locked and user.status == "active":
        if need_code:
            secret = get_cipher().decrypt(user.totp_secret_encrypted, _totp_context(user.id))
            step = verify_totp(secret, totp_code or "", user.totp_last_step)
            passed = step is not None
        else:
            passed = True

    if user is None or not passed:
        reason = "unknown_login_id" if user is None else "locked" if locked else "bad_credentials"
        if user is not None and not locked:
            user.failed_logins += 1
            if user.failed_logins >= settings.admin_max_failed_logins:
                user.locked_until = now + timedelta(minutes=settings.admin_lockout_minutes)
                user.failed_logins = 0
                reason = "locked_now"
        await record(
            session,
            Actor("admin", str(user.id) if user else None, ip=ip),
            "admin.sign_in_failed",
            "admin_user",
            str(user.id) if user else None,
            after={"reason": reason},
        )
        await session.commit()
        raise InvalidCredentials()

    user.failed_logins = 0
    user.locked_until = None
    if step is not None:
        user.totp_last_step = step
    user.last_login_at = now
    if password_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    token, token_hash = new_token(TOKEN_PREFIX)
    admin_session = AdminSession(
        admin_id=user.id,
        token_hash=token_hash,
        last_seen_at=now,
        expires_at=now + timedelta(hours=settings.admin_session_max_hours),
        ip=ip,
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(admin_session)
    await session.flush()
    await record(
        session,
        Actor("admin", str(user.id), ip=ip),
        "admin.signed_in",
        "admin_session",
        str(admin_session.id),
        after={"authenticator_code": "checked" if need_code else "not required (development)"},
    )
    await session.commit()
    return SignIn(token=token, session=admin_session, user=user)


@dataclass(frozen=True)
class AdminContext:
    user: AdminUser
    session: AdminSession
    actor: Actor

    def has_any(self, roles: set[AdminRole]) -> bool:
        return bool(roles & {AdminRole(r) for r in self.user.roles})


async def resolve_session(session: AsyncSession, token: str, ip: str | None = None) -> AdminContext:
    """Return the signed-in admin for a bearer token, refreshing its idle timer. Raises SessionInvalid otherwise."""
    if not token.startswith(f"{TOKEN_PREFIX}_"):
        raise SessionInvalid()  # e.g. a customer token sent to the admin API
    row = (
        await session.execute(
            select(AdminSession, AdminUser)
            .join(AdminUser, AdminUser.id == AdminSession.admin_id)
            .where(AdminSession.token_hash == hash_token(token), AdminSession.revoked_at.is_(None))
        )
    ).first()
    if row is None:
        raise SessionInvalid()
    admin_session, user = row

    now = _now()
    idle_limit = timedelta(minutes=get_settings().admin_session_idle_minutes)
    if admin_session.expires_at <= now or admin_session.last_seen_at + idle_limit <= now or user.status != "active":
        admin_session.revoked_at = now
        await session.commit()
        raise SessionInvalid()

    admin_session.last_seen_at = now
    await session.commit()
    return AdminContext(user=user, session=admin_session, actor=Actor("admin", str(user.id), ip=ip))


async def sign_out(session: AsyncSession, context: AdminContext) -> None:
    context.session.revoked_at = _now()
    await record(session, context.actor, "admin.signed_out", "admin_session", str(context.session.id))
    await session.commit()
