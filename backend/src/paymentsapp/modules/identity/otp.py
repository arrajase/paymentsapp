"""One-time codes (plan M1).

- 6 digits, valid for a few minutes, usable once, stored only as a keyed hash.
- A challenge dies after a few wrong guesses.
- Requests are limited per identifier (the otp_requests_per_hour control), per IP, and by a resend cooldown, all
  counted in Postgres.
- Requesting a code always gets the same answer, whether or not an account exists.
"""

import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import get_settings
from paymentsapp.core.crypto import blind_index, get_cipher, keyed_hash
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.messaging.base import MessagingUnavailable
from paymentsapp.modules.audit.service import Actor, record
from paymentsapp.modules.controls.service import get_value
from paymentsapp.modules.identity.identifiers import IdentifierKind, normalise
from paymentsapp.modules.identity.models import OtpChallenge
from paymentsapp.modules.notifications.service import send_otp


class InvalidCode(AppError):
    def __init__(self) -> None:
        super().__init__("INVALID_CODE", "the code is wrong or has expired; request a new one", status_code=401)


def _rate_limited(retry_after: int) -> AppError:
    return AppError(
        "RATE_LIMITED",
        "too many codes requested; please wait and try again",
        status_code=429,
        headers={"Retry-After": str(max(1, retry_after))},
    )


def _now() -> datetime:
    return datetime.now(UTC)


def challenge_context(challenge_id: uuid.UUID) -> str:
    return f"otp_challenges.value:{challenge_id}"


def _code_hash(challenge_id: uuid.UUID, code: str) -> str:
    return keyed_hash("otp", f"{challenge_id}:{code}")


@dataclass(frozen=True)
class OtpRequested:
    challenge_id: uuid.UUID
    masked: str
    expires_at: datetime
    resend_after_seconds: int


async def request_otp(
    session: AsyncSession,
    kind: IdentifierKind,
    raw_identifier: str,
    *,
    purpose: str = "sign_in",
    customer_id: uuid.UUID | None = None,
    ip: str | None = None,
) -> OtpRequested:
    settings = get_settings()
    try:
        identifier = normalise(kind, raw_identifier)
    except ValueError as exc:
        raise AppError("INVALID_IDENTIFIER", str(exc), status_code=422) from None
    index = blind_index(kind, identifier.value)
    now = _now()
    hour_ago = now - timedelta(hours=1)

    last = await session.scalar(select(func.max(OtpChallenge.created_at)).where(OtpChallenge.blind_index == index))
    if last is not None:
        wait = settings.otp_resend_cooldown_seconds - int((now - last).total_seconds())
        if wait > 0:
            raise _rate_limited(wait)

    hourly = int(await get_value(session, "otp_requests_per_hour"))
    per_identifier = await session.scalar(
        select(func.count()).where(OtpChallenge.blind_index == index, OtpChallenge.created_at > hour_ago)
    )
    if (per_identifier or 0) >= hourly:
        raise _rate_limited(3600)
    if ip:
        per_ip = await session.scalar(
            select(func.count()).where(OtpChallenge.request_ip == ip, OtpChallenge.created_at > hour_ago)
        )
        if (per_ip or 0) >= hourly * settings.otp_ip_hourly_multiplier:
            raise _rate_limited(3600)

    challenge_id = uuid.uuid4()
    code = f"{secrets.randbelow(1_000_000):06d}"
    expires_at = now + timedelta(seconds=settings.otp_ttl_seconds)
    session.add(
        OtpChallenge(
            id=challenge_id,
            kind=kind,
            blind_index=index,
            value_encrypted=get_cipher().encrypt(identifier.value, challenge_context(challenge_id)),
            purpose=purpose,
            customer_id=customer_id,
            code_hash=_code_hash(challenge_id, code),
            attempts=0,
            expires_at=expires_at,
            request_ip=ip,
        )
    )
    await session.flush()
    try:
        await send_otp(session, kind, identifier.value, code, settings.otp_ttl_seconds // 60)
    except MessagingUnavailable:
        raise AppError("CANNOT_SEND", "we can't send codes right now; try again later", status_code=503) from None

    await record(
        session,
        Actor("customer", str(customer_id) if customer_id else None, ip=ip),
        "customer.otp_requested",
        "identifier",
        identifier.masked,
        after={"kind": kind, "purpose": purpose, "challenge_id": str(challenge_id)},
    )
    await session.commit()
    return OtpRequested(challenge_id, identifier.masked, expires_at, settings.otp_resend_cooldown_seconds)


async def verify_otp(session: AsyncSession, challenge_id: uuid.UUID, code: str, purpose: str) -> OtpChallenge:
    """Consume the challenge if the code is right. Every wrong guess counts; too many and the challenge dies."""
    challenge = await session.get(OtpChallenge, challenge_id, with_for_update=True)
    now = _now()
    if challenge is None or challenge.purpose != purpose or challenge.consumed_at or challenge.expires_at <= now:
        raise InvalidCode()

    challenge.attempts += 1
    if not hmac.compare_digest(_code_hash(challenge.id, code.strip()), challenge.code_hash):
        if challenge.attempts >= get_settings().otp_max_attempts:
            challenge.consumed_at = now  # dead: the customer must request a new code
        await record(
            session,
            Actor("customer", str(challenge.customer_id) if challenge.customer_id else None, ip=challenge.request_ip),
            "customer.otp_failed",
            "otp_challenge",
            str(challenge.id),
            after={"attempts": challenge.attempts, "exhausted": challenge.consumed_at is not None},
        )
        await session.commit()
        raise InvalidCode()

    challenge.consumed_at = now
    await session.flush()
    return challenge


def challenge_value(challenge: OtpChallenge) -> str:
    return get_cipher().decrypt(challenge.value_encrypted, challenge_context(challenge.id))
