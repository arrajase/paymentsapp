"""Step-up authentication before a sensitive action (plan M1).

The signed-in app asks for a challenge naming the action, signs it with the device key and sends the app PIN (or
confirms a biometric unlock of the key). It gets back a step-up token that is valid for a few minutes, for that one
action, in that session, once. The action's endpoint takes the token in the X-Step-Up-Token header.
"""

import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import get_settings
from paymentsapp.core.errors import AppError
from paymentsapp.core.security import hash_token, new_token
from paymentsapp.modules.audit.service import record
from paymentsapp.modules.identity.devices import step_up_message, verify_signature
from paymentsapp.modules.identity.models import DeviceChallenge, StepUpToken
from paymentsapp.modules.identity.service import Challenge, check_pin
from paymentsapp.modules.identity.sessions import CustomerContext

StepUpAction = Literal["upi_link", "upi_unlink", "upi_set_default"]
STEP_UP_PREFIX = "stp"


def _now() -> datetime:
    return datetime.now(UTC)


class StepUpRequired(AppError):
    def __init__(self) -> None:
        # 403, not 401: the session is fine, and a 401 would make the app try to refresh its tokens.
        super().__init__("STEP_UP_REQUIRED", "confirm with your app PIN or fingerprint first", status_code=403)


async def create_step_up_challenge(session: AsyncSession, context: CustomerContext, action: StepUpAction) -> Challenge:
    expires_at = _now() + timedelta(seconds=get_settings().device_challenge_seconds)
    challenge = DeviceChallenge(
        device_id=context.device.id, nonce=secrets.token_urlsafe(32), action=action, expires_at=expires_at
    )
    session.add(challenge)
    await session.commit()
    return Challenge(challenge.id, challenge.nonce, expires_at)


@dataclass(frozen=True)
class StepUp:
    token: str
    action: str
    expires_at: datetime


async def verify_step_up(
    session: AsyncSession,
    context: CustomerContext,
    *,
    challenge_id: uuid.UUID,
    signature: str,
    pin: str | None,
    biometric: bool,
) -> StepUp:
    if not pin and not biometric:
        raise AppError("SECOND_FACTOR_REQUIRED", "enter your app PIN or use your fingerprint", status_code=422)
    challenge = await session.get(DeviceChallenge, challenge_id, with_for_update=True)
    now = _now()
    if (
        challenge is None
        or challenge.action is None
        or challenge.device_id != context.device.id
        or challenge.used_at is not None
        or challenge.expires_at <= now
    ):
        raise AppError("CHALLENGE_EXPIRED", "this confirmation has expired; try again", status_code=401)
    challenge.used_at = now  # one attempt per challenge, right or wrong

    message = step_up_message(str(challenge.id), challenge.nonce, challenge.action)
    if not verify_signature(context.device.public_key, message, signature):
        await record(session, context.actor, "customer.device_signature_failed", "device", str(context.device.id))
        await session.commit()
        raise AppError("CHALLENGE_EXPIRED", "this confirmation has expired; try again", status_code=401)
    if pin:
        await check_pin(session, context.customer, pin, context.actor)

    token, token_hash = new_token(STEP_UP_PREFIX)
    expires_at = now + timedelta(minutes=get_settings().step_up_token_minutes)
    session.add(
        StepUpToken(
            token_hash=token_hash, session_id=context.session.id, action=challenge.action, expires_at=expires_at
        )
    )
    await record(
        session,
        context.actor,
        "customer.step_up",
        "customer_session",
        str(context.session.id),
        after={"action": challenge.action, "method": "pin" if pin else "biometric"},
    )
    await session.commit()
    return StepUp(token, challenge.action, expires_at)


async def consume_step_up(
    session: AsyncSession, context: CustomerContext, token: str | None, action: StepUpAction
) -> None:
    """Use up a step-up token for `action`. Joins the caller's transaction; the caller commits."""
    if not token or not token.startswith(f"{STEP_UP_PREFIX}_"):
        raise StepUpRequired()
    stored = await session.get(StepUpToken, hash_token(token), with_for_update=True)
    if (
        stored is None
        or stored.session_id != context.session.id
        or stored.action != action
        or stored.used_at is not None
        or stored.expires_at <= _now()
    ):
        raise StepUpRequired()
    stored.used_at = _now()
    await session.flush()
