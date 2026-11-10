"""KYC tiers (plan M2, slim). Validation happens here in the payments app; the bank isn't involved.

Full KYC goes through the KYC plugin. If no KYC plugin is enabled, upgrades pause: customers stay at their current
tier and are never upgraded without a check.
"""

import re
import secrets
import uuid
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.errors import AppError
from paymentsapp.integrations.registry import plugin_enabled
from paymentsapp.modules.audit.service import Actor, record
from paymentsapp.modules.identity.queries import has_verified_identifier
from paymentsapp.modules.kyc.models import KycProfile

IST = ZoneInfo("Asia/Kolkata")
MINIMUM_AGE = 18
_NAME = re.compile(r"^[A-Za-z][A-Za-z .'-]{1,99}$")


def _now() -> datetime:
    return datetime.now(UTC)


def age_on(date_of_birth: date, today: date) -> int:
    had_birthday = (today.month, today.day) >= (date_of_birth.month, date_of_birth.day)
    return today.year - date_of_birth.year - (0 if had_birthday else 1)


async def create_profile(session: AsyncSession, customer_id: uuid.UUID) -> KycProfile:
    profile = KycProfile(customer_id=customer_id, tier="T0")
    session.add(profile)
    await session.flush()
    return profile


async def get_profile(session: AsyncSession, customer_id: uuid.UUID) -> KycProfile:
    profile = await session.get(KycProfile, customer_id)
    if profile is None:
        profile = await create_profile(session, customer_id)
    return profile


async def submit_minimum_kyc(
    session: AsyncSession, customer_id: uuid.UUID, actor: Actor, full_name: str, date_of_birth: date
) -> KycProfile:
    profile = await session.get(KycProfile, customer_id, with_for_update=True) or await create_profile(
        session, customer_id
    )
    if profile.tier != "T0":
        raise AppError("ALREADY_DONE", "minimum KYC is already complete", status_code=409)

    name = " ".join(full_name.split())
    if not _NAME.match(name):
        raise AppError("INVALID_NAME", "enter your full name as it appears on your ID, using letters only", 422)
    today = datetime.now(IST).date()
    if date_of_birth > today:
        raise AppError("INVALID_DATE_OF_BIRTH", "the date of birth is in the future", status_code=422)
    if age_on(date_of_birth, today) < MINIMUM_AGE:
        raise AppError("TOO_YOUNG", f"you must be at least {MINIMUM_AGE} to open a wallet", status_code=422)
    if not await has_verified_identifier(session, customer_id, "mobile"):
        raise AppError("MOBILE_REQUIRED", "verify a mobile number first", status_code=409)

    profile.full_name = name
    profile.date_of_birth = date_of_birth
    profile.tier = "T1"
    profile.minimum_kyc_at = _now()
    await session.flush()
    await record(session, actor, "kyc.minimum_completed", "customer", str(customer_id), after={"tier": "T1"})
    await session.commit()
    return profile


async def upgrade_to_full_kyc(session: AsyncSession, customer_id: uuid.UUID, actor: Actor) -> KycProfile:
    profile = await get_profile(session, customer_id)
    if profile.tier == "T2":
        raise AppError("ALREADY_DONE", "full KYC is already complete", status_code=409)
    if profile.tier != "T1":
        raise AppError("MINIMUM_KYC_REQUIRED", "complete minimum KYC first", status_code=409)
    if not await plugin_enabled(session, "mock_kyc"):
        raise AppError("KYC_UNAVAILABLE", "verification is temporarily unavailable; try again later", 503)

    # The mock plugin approves at once. A real KYC vendor plugin would start a verification here instead.
    profile.tier = "T2"
    profile.full_kyc_at = _now()
    profile.full_kyc_provider = "mock_kyc"
    profile.full_kyc_reference = f"MOCK-{secrets.token_hex(6).upper()}"
    await session.flush()
    await record(
        session,
        actor,
        "kyc.full_completed",
        "customer",
        str(customer_id),
        after={"tier": "T2", "provider": "mock_kyc", "reference": profile.full_kyc_reference},
    )
    await session.commit()
    return profile
