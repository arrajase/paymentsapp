"""Customer sessions and tokens (plan M1).

- Access tokens (cus_...) are short-lived and checked on every request.
- Refresh tokens (cur_...) rotate on every use and are bound to the device. Presenting one that was already used
  means it was copied, so the whole session is revoked.
- Only hashes of tokens are stored.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import get_settings
from paymentsapp.core.errors import AppError
from paymentsapp.core.security import hash_token, new_token
from paymentsapp.modules.audit.service import Actor, record
from paymentsapp.modules.identity.models import Customer, CustomerSession, CustomerToken, Device

ACCESS_PREFIX = "cus"
REFRESH_PREFIX = "cur"


class SessionInvalid(AppError):
    def __init__(self) -> None:
        super().__init__("UNAUTHORIZED", "sign in again", status_code=401)


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime
    session_id: uuid.UUID


async def _issue(session: AsyncSession, customer_session: CustomerSession, now: datetime) -> TokenPair:
    settings = get_settings()
    access, access_hash = new_token(ACCESS_PREFIX)
    refresh, refresh_hash = new_token(REFRESH_PREFIX)
    access_expires = now + timedelta(minutes=settings.customer_access_token_minutes)
    refresh_expires = now + timedelta(days=settings.customer_refresh_token_days)
    session.add_all(
        [
            CustomerToken(
                token_hash=access_hash, session_id=customer_session.id, kind="access", expires_at=access_expires
            ),
            CustomerToken(
                token_hash=refresh_hash, session_id=customer_session.id, kind="refresh", expires_at=refresh_expires
            ),
        ]
    )
    await session.flush()
    return TokenPair(access, access_expires, refresh, refresh_expires, customer_session.id)


async def start_session(session: AsyncSession, customer_id: uuid.UUID, device_id: uuid.UUID) -> TokenPair:
    now = _now()
    customer_session = CustomerSession(customer_id=customer_id, device_id=device_id, last_used_at=now)
    session.add(customer_session)
    await session.flush()
    return await _issue(session, customer_session, now)


async def revoke_device_sessions(session: AsyncSession, device_id: uuid.UUID, reason: str) -> None:
    await session.execute(
        update(CustomerSession)
        .where(CustomerSession.device_id == device_id, CustomerSession.revoked_at.is_(None))
        .values(revoked_at=_now(), revoke_reason=reason)
    )


async def refresh(session: AsyncSession, refresh_token: str, device_id: uuid.UUID, ip: str | None) -> TokenPair:
    if not refresh_token.startswith(f"{REFRESH_PREFIX}_"):
        raise SessionInvalid()
    token = await session.get(CustomerToken, hash_token(refresh_token), with_for_update=True)
    if token is None or token.kind != "refresh":
        raise SessionInvalid()
    customer_session = await session.get(CustomerSession, token.session_id, with_for_update=True)
    if customer_session is None or customer_session.revoked_at is not None:
        raise SessionInvalid()
    now = _now()
    actor = Actor("customer", str(customer_session.customer_id), ip=ip, device_id=str(device_id))

    reason = None
    if customer_session.device_id != device_id:
        reason = "device_mismatch"
    elif token.used_at is not None:
        reason = "refresh_reuse"  # an old refresh token came back: someone has a copy
    if reason:
        customer_session.revoked_at = now
        customer_session.revoke_reason = reason
        await record(
            session,
            actor,
            "customer.session_revoked",
            "customer_session",
            str(customer_session.id),
            after={"reason": reason},
        )
        await session.commit()
        raise SessionInvalid()
    if token.expires_at <= now:
        raise SessionInvalid()

    token.used_at = now
    customer_session.last_used_at = now
    pair = await _issue(session, customer_session, now)
    await session.commit()
    return pair


@dataclass(frozen=True)
class CustomerContext:
    customer: Customer
    session: CustomerSession
    device: Device
    actor: Actor


async def resolve_access(session: AsyncSession, access_token: str, ip: str | None = None) -> CustomerContext:
    """The signed-in customer for an access token. Admin tokens, refresh tokens and anything else are rejected."""
    if not access_token.startswith(f"{ACCESS_PREFIX}_"):
        raise SessionInvalid()
    row = (
        await session.execute(
            select(CustomerToken, CustomerSession, Device, Customer)
            .join(CustomerSession, CustomerSession.id == CustomerToken.session_id)
            .join(Device, Device.id == CustomerSession.device_id)
            .join(Customer, Customer.id == CustomerSession.customer_id)
            .where(CustomerToken.token_hash == hash_token(access_token), CustomerToken.kind == "access")
        )
    ).first()
    if row is None:
        raise SessionInvalid()
    token, customer_session, device, customer = row
    if (
        token.expires_at <= _now()
        or customer_session.revoked_at is not None
        or device.status != "active"
        or customer.status != "active"
    ):
        raise SessionInvalid()
    actor = Actor("customer", str(customer.id), ip=ip, device_id=str(device.id))
    return CustomerContext(customer=customer, session=customer_session, device=device, actor=actor)


async def sign_out(session: AsyncSession, context: CustomerContext) -> None:
    context.session.revoked_at = _now()
    context.session.revoke_reason = "signed_out"
    await record(session, context.actor, "customer.signed_out", "customer_session", str(context.session.id))
    await session.commit()
