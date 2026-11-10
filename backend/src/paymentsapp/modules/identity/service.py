"""Customer sign-up, sign-in and devices (plan M1).

First time on a device:
  1. request_otp (otp.py) sends a code to the email or mobile number.
  2. verify_sign_in_code checks it and returns a short-lived enrolment token, saying what comes next:
     "create_account" (new customer), "set_pin" or "enter_pin".
  3. enrol_device registers the device's public key, sets or checks the app PIN and starts a session.
     A new device replaces the customer's previous one: one active device per customer.

Returning on the same device:
  4. create_challenge gives a one-time nonce; sign_in_with_device checks the device's signature over it plus the
     app PIN (or a biometric unlock of the device key) and starts a session. No OTP needed.
"""

import contextlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import get_settings
from paymentsapp.core.crypto import blind_index, get_cipher
from paymentsapp.core.errors import AppError
from paymentsapp.core.security import hash_password, hash_token, new_token, verify_password
from paymentsapp.integrations.messaging.base import MessagingUnavailable
from paymentsapp.modules.audit.service import Actor, record
from paymentsapp.modules.consent import service as consent
from paymentsapp.modules.controls.service import get_value
from paymentsapp.modules.identity.devices import InvalidDeviceKey, load_public_key, sign_in_message, verify_signature
from paymentsapp.modules.identity.identifiers import normalise, pin_problem
from paymentsapp.modules.identity.models import (
    Customer,
    CustomerIdentifier,
    Device,
    DeviceChallenge,
    EnrolmentToken,
    OtpChallenge,
)
from paymentsapp.modules.identity.otp import InvalidCode, challenge_value, verify_otp
from paymentsapp.modules.identity.queries import contact_points, identifier_context
from paymentsapp.modules.identity.sessions import TokenPair, revoke_device_sessions, start_session
from paymentsapp.modules.kyc.service import create_profile
from paymentsapp.modules.notifications.service import send_security_alert

ENROLMENT_PREFIX = "enr"


def _now() -> datetime:
    return datetime.now(UTC)


class DeviceUnknown(AppError):
    def __init__(self) -> None:
        super().__init__("DEVICE_NOT_RECOGNISED", "this device isn't signed in; sign in with a code", status_code=401)


# ---- Step 2: verify the code -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class CodeVerified:
    enrolment_token: str
    expires_at: datetime
    next_step: str  # "create_account", "set_pin" or "enter_pin"


async def verify_sign_in_code(session: AsyncSession, challenge_id: uuid.UUID, code: str) -> CodeVerified:
    challenge = await verify_otp(session, challenge_id, code, purpose="sign_in")
    identifier = await session.scalar(
        select(CustomerIdentifier).where(
            CustomerIdentifier.kind == challenge.kind, CustomerIdentifier.blind_index == challenge.blind_index
        )
    )
    customer = await session.get(Customer, identifier.customer_id) if identifier else None
    if customer is None:
        if not await get_value(session, "customer_signup"):
            await session.commit()  # the code is used up either way
            raise AppError("SIGNUP_CLOSED", "new sign-ups are paused; please try again later", status_code=403)
        next_step = "create_account"
    elif customer.status != "active":
        await session.commit()
        raise AppError("ACCOUNT_LOCKED", "this account is locked; contact support", status_code=403)
    else:
        next_step = "enter_pin" if customer.pin_hash else "set_pin"

    token, token_hash = new_token(ENROLMENT_PREFIX)
    expires_at = _now() + timedelta(minutes=get_settings().enrolment_token_minutes)
    session.add(
        EnrolmentToken(
            token_hash=token_hash,
            challenge_id=challenge.id,
            customer_id=customer.id if customer else None,
            expires_at=expires_at,
        )
    )
    await record(
        session,
        Actor("customer", str(customer.id) if customer else None, ip=challenge.request_ip),
        "customer.otp_verified",
        "otp_challenge",
        str(challenge.id),
        after={"next_step": next_step},
    )
    await session.commit()
    return CodeVerified(token, expires_at, next_step)


# ---- PIN checks ------------------------------------------------------------------------------------------------


async def check_pin(session: AsyncSession, customer: Customer, pin: str, actor: Actor) -> None:
    """Verify the app PIN with an attempt counter. Commits failures so they count even though the request fails."""
    settings = get_settings()
    now = _now()
    if customer.pin_locked_until is not None and customer.pin_locked_until > now:
        raise AppError("PIN_LOCKED", "too many wrong PINs; try again later", status_code=423)
    if customer.pin_hash and verify_password(customer.pin_hash, pin):
        customer.pin_failed_attempts = 0
        return
    customer.pin_failed_attempts += 1
    locked = customer.pin_failed_attempts >= settings.pin_max_attempts
    if locked:
        customer.pin_locked_until = now + timedelta(minutes=settings.pin_lockout_minutes)
        customer.pin_failed_attempts = 0
    await record(session, actor, "customer.pin_failed", "customer", str(customer.id), after={"locked": locked})
    await session.commit()
    raise AppError("INVALID_PIN", "the app PIN is incorrect", status_code=401)


# ---- Step 3: register the device ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Enrolled:
    customer_id: uuid.UUID
    device_id: uuid.UUID
    tokens: TokenPair
    new_customer: bool
    replaced_device: bool


async def enrol_device(
    session: AsyncSession,
    *,
    enrolment_token: str,
    public_key: str,
    platform: str,
    hardware_backed: bool,
    label: str | None,
    pin: str,
    accepted_terms_version: str | None,
    ip: str | None,
) -> Enrolled:
    enrolment = await session.get(EnrolmentToken, hash_token(enrolment_token), with_for_update=True)
    now = _now()
    if enrolment is None or enrolment.used_at is not None or enrolment.expires_at <= now:
        raise AppError("ENROLMENT_EXPIRED", "this step has expired; start again with a new code", status_code=401)
    challenge = await session.get(OtpChallenge, enrolment.challenge_id)
    assert challenge is not None

    try:
        load_public_key(public_key)
    except InvalidDeviceKey as exc:
        raise AppError("INVALID_DEVICE_KEY", str(exc), status_code=422) from None
    if await get_value(session, "hardware_device_key") == "enforce" and not hardware_backed:
        raise AppError("DEVICE_NOT_ALLOWED", "this device's key isn't protected by secure hardware", status_code=403)

    new_customer = enrolment.customer_id is None
    if new_customer:
        terms = consent.notices()[consent.TERMS]
        if accepted_terms_version != terms.version:
            raise AppError("TERMS_REQUIRED", f"accept the terms and privacy notice (version {terms.version})", 422)
        problem = pin_problem(pin)
        if problem:
            raise AppError("WEAK_PIN", problem, status_code=422)
        customer = Customer(status="active", pin_hash=hash_password(pin), pin_failed_attempts=0)
        session.add(customer)
        await session.flush()
        identifier = CustomerIdentifier(
            id=uuid.uuid4(),
            customer_id=customer.id,
            kind=challenge.kind,
            blind_index=challenge.blind_index,
            verified_at=now,
            value_encrypted="",
            masked="",
        )
        value = challenge_value(challenge)
        identifier.value_encrypted = get_cipher().encrypt(value, identifier_context(identifier.id))
        identifier.masked = normalise(challenge.kind, value).masked  # type: ignore[arg-type]
        session.add(identifier)
        try:
            await session.flush()
        except IntegrityError:
            # The same email or number finished sign-up in another request a moment ago.
            await session.rollback()
            raise AppError("ENROLMENT_EXPIRED", "this step has expired; start again with a new code", 401) from None
        await create_profile(session, customer.id)
        actor = Actor("customer", str(customer.id), ip=ip)
        await consent.grant(session, customer.id, actor, consent.TERMS, terms.version)
        await record(
            session, actor, "customer.created", "customer", str(customer.id), after={"identifier": identifier.masked}
        )
    else:
        existing = await session.get(Customer, enrolment.customer_id, with_for_update=True)
        assert existing is not None
        customer = existing
        actor = Actor("customer", str(customer.id), ip=ip)
        if customer.pin_hash is None:
            problem = pin_problem(pin)
            if problem:
                raise AppError("WEAK_PIN", problem, status_code=422)
            customer.pin_hash = hash_password(pin)
        else:
            await check_pin(session, customer, pin, actor)

    previous = await session.scalar(
        select(Device).where(Device.customer_id == customer.id, Device.status == "active").with_for_update()
    )
    if previous is not None:
        previous.status = "revoked"
        previous.revoked_at = now
        await revoke_device_sessions(session, previous.id, "new_device")
        await session.flush()

    cooling_hours = int(await get_value(session, "new_device_cooling_hours")) if previous else 0
    device = Device(
        customer_id=customer.id,
        public_key=public_key,
        hardware_backed=hardware_backed,
        platform=platform,
        label=(label or "")[:100] or None,
        status="active",
        cooling_until=now + timedelta(hours=cooling_hours) if cooling_hours else None,
        last_seen_at=now,
    )
    session.add(device)
    await session.flush()
    tokens = await start_session(session, customer.id, device.id)
    enrolment.used_at = now
    await record(
        session,
        Actor("customer", str(customer.id), ip=ip, device_id=str(device.id)),
        "customer.device_registered",
        "device",
        str(device.id),
        after={
            "platform": platform,
            "hardware_backed": hardware_backed,
            "replaced_device": str(previous.id) if previous else None,
            "cooling_until": device.cooling_until.isoformat() if device.cooling_until else None,
        },
    )
    if previous is not None:
        # The change itself must not fail because an alert couldn't be sent.
        with contextlib.suppress(MessagingUnavailable):
            await send_security_alert(
                session,
                await contact_points(session, customer.id),
                f"your account was signed in on a new device ({platform}). Your other device was signed out.",
            )
    await session.commit()
    return Enrolled(customer.id, device.id, tokens, new_customer, previous is not None)


# ---- Step 4: returning sign-in on the same device ----------------------------------------------------------------


@dataclass(frozen=True)
class Challenge:
    challenge_id: uuid.UUID
    nonce: str
    expires_at: datetime


async def create_challenge(session: AsyncSession, device_id: uuid.UUID) -> Challenge:
    device = await session.get(Device, device_id)
    if device is None or device.status != "active":
        raise DeviceUnknown()
    expires_at = _now() + timedelta(seconds=get_settings().device_challenge_seconds)
    challenge = DeviceChallenge(device_id=device.id, nonce=secrets.token_urlsafe(32), expires_at=expires_at)
    session.add(challenge)
    await session.commit()
    return Challenge(challenge.id, challenge.nonce, expires_at)


async def sign_in_with_device(
    session: AsyncSession,
    *,
    device_id: uuid.UUID,
    challenge_id: uuid.UUID,
    signature: str,
    pin: str | None,
    biometric: bool,
    ip: str | None,
) -> TokenPair:
    if not pin and not biometric:
        raise AppError("SECOND_FACTOR_REQUIRED", "enter your app PIN or use your fingerprint", status_code=422)
    challenge = await session.get(DeviceChallenge, challenge_id, with_for_update=True)
    device = await session.get(Device, device_id)
    now = _now()
    if (
        challenge is None
        or device is None
        or challenge.device_id != device.id
        or challenge.action is not None  # a step-up challenge can't be used to sign in
        or device.status != "active"
        or challenge.used_at is not None
        or challenge.expires_at <= now
    ):
        raise DeviceUnknown()
    challenge.used_at = now  # one attempt per challenge, right or wrong
    actor = Actor("customer", str(device.customer_id), ip=ip, device_id=str(device.id))

    if not verify_signature(device.public_key, sign_in_message(str(challenge.id), challenge.nonce), signature):
        await record(session, actor, "customer.device_signature_failed", "device", str(device.id))
        await session.commit()
        raise DeviceUnknown()

    customer = await session.get(Customer, device.customer_id, with_for_update=True)
    assert customer is not None
    if customer.status != "active":
        await session.commit()
        raise AppError("ACCOUNT_LOCKED", "this account is locked; contact support", status_code=403)
    if pin:
        await check_pin(session, customer, pin, actor)

    device.last_seen_at = now
    tokens = await start_session(session, customer.id, device.id)
    await record(
        session,
        actor,
        "customer.signed_in",
        "customer_session",
        str(tokens.session_id),
        after={"method": "pin" if pin else "biometric"},
    )
    await session.commit()
    return tokens


# ---- Adding a second identifier ----------------------------------------------------------------------------------


async def add_identifier(
    session: AsyncSession, customer_id: uuid.UUID, actor: Actor, challenge_id: uuid.UUID, code: str
) -> CustomerIdentifier:
    challenge = await verify_otp(session, challenge_id, code, purpose="add_identifier")
    if challenge.customer_id != customer_id:
        raise InvalidCode()
    taken = await session.scalar(
        select(CustomerIdentifier.id).where(
            CustomerIdentifier.kind == challenge.kind, CustomerIdentifier.blind_index == challenge.blind_index
        )
    )
    if taken:
        await session.commit()
        raise AppError("IDENTIFIER_IN_USE", "this is already linked to another account", status_code=409)
    value = challenge_value(challenge)
    identifier = CustomerIdentifier(
        id=uuid.uuid4(),
        customer_id=customer_id,
        kind=challenge.kind,
        blind_index=blind_index(challenge.kind, value),
        verified_at=_now(),
        value_encrypted="",
        masked=normalise(challenge.kind, value).masked,  # type: ignore[arg-type]
    )
    identifier.value_encrypted = get_cipher().encrypt(value, identifier_context(identifier.id))
    session.add(identifier)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise AppError("IDENTIFIER_EXISTS", f"you already have a verified {challenge.kind}", status_code=409) from None
    await record(
        session,
        actor,
        "customer.identifier_added",
        "customer",
        str(customer_id),
        after={"kind": challenge.kind, "identifier": identifier.masked},
    )
    with contextlib.suppress(MessagingUnavailable):
        await send_security_alert(
            session, await contact_points(session, customer_id), f"a new {challenge.kind} was added to your account."
        )
    await session.commit()
    return identifier
