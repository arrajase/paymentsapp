"""UPI linking (plan M3), against the bank chosen by the plugin registry.

1. Find the account: discover() asks the bank which UPI ID it links to the customer's verified mobile number, or
   validate() checks a UPI ID the customer typed. Either way the customer sees only a masked account-holder name.
2. Checks: an active INR account, a name that matches the KYC name and, when the sim_binding control is enforced,
   a UPI ID the bank links to the verified mobile.
3. create_link() (after step-up) asks the bank for a LINK consent. The customer approves it with their UPI PIN on
   the bank's website; the approved consent is the proof of ownership, and only then is the link active.
4. The consent's state arrives by webhook (on_consent_updated), and is also re-checked whenever the customer lists
   their links, because a bank hosted elsewhere can't reach a backend running on a laptop.

Prerequisites: the upi_linking feature switch, KYC T1 or higher (which needs a verified mobile) and, for a new
link, a device past its cooling period. Calls to the bank's verify and lookup count towards the customer's
vpa_verifications_per_hour limit, so the checks can't be used to harvest other people's names.
"""

import contextlib
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.crypto import blind_index, get_cipher
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.bank_gateway import (
    BankError,
    BankUnavailable,
    Consent,
    ConsentPurpose,
    ConsentStatus,
    PaymentProvider,
    VpaVerification,
)
from paymentsapp.integrations.messaging.base import MessagingUnavailable
from paymentsapp.integrations.registry import MOCKBANK, ProviderUnavailable, get_payment_provider
from paymentsapp.modules.audit.service import Actor, record
from paymentsapp.modules.controls.service import get_value
from paymentsapp.modules.identity.queries import contact_points, verified_mobile
from paymentsapp.modules.identity.sessions import CustomerContext
from paymentsapp.modules.identity.step_up import consume_step_up
from paymentsapp.modules.kyc.models import KycProfile
from paymentsapp.modules.kyc.service import get_profile
from paymentsapp.modules.notifications.service import send_security_alert
from paymentsapp.modules.plugins.catalogue import UPI_ID_PATTERN
from paymentsapp.modules.upi.models import UpiCheck, UpiLink
from paymentsapp.modules.upi.name_match import mask_name, mask_upi_id, match_score, names_match

log = logging.getLogger(__name__)

MAX_LINKS = 3  # pending and active together
LINK_CONSENT_DAYS = 365
APPROVAL_WINDOW = timedelta(minutes=30)  # a link not approved at the bank by then fails
RECENT_FAILURES = timedelta(days=1)  # failed links stay in the list this long, so the customer sees why
OPEN = ("pending", "active")
BANK_ACTOR = Actor("system", "bank-consent")


def _now() -> datetime:
    return datetime.now(UTC)


def _upi_context(link_id: uuid.UUID) -> str:
    return f"upi_links.upi_id:{link_id}"


def normalise_upi_id(raw: str) -> str:
    upi_id = raw.strip().lower()
    if not UPI_ID_PATTERN.match(upi_id):
        raise AppError("INVALID_UPI_ID", "enter a UPI ID such as name@bank", status_code=422)
    return upi_id


def link_upi_id(link: UpiLink) -> str:
    return get_cipher().decrypt(link.upi_id_encrypted, _upi_context(link.id))


# ---- Bank calls ------------------------------------------------------------------------------------------------


def _bank_failed(exc: BankError) -> AppError:
    if isinstance(exc, BankUnavailable):
        return AppError("BANK_UNAVAILABLE", "the bank isn't responding; try again in a minute", status_code=503)
    if exc.code == "UPI_NOT_FOUND":
        return AppError("UPI_NOT_FOUND", "no bank account has this UPI ID", status_code=404)
    if exc.code == "RATE_LIMITED":
        return AppError("RATE_LIMITED", "the bank is busy; try again in a minute", status_code=429)
    log.warning("bank refused a UPI linking call", extra={"code": exc.code})
    return AppError("BANK_ERROR", "the bank couldn't process this; try again later", status_code=502)


async def _record_check(session: AsyncSession, context: CustomerContext, kind: str) -> None:
    """Count one verify or lookup against the customer's hourly limit. Committed first, so it counts even if the
    bank call then fails."""
    limit = int(await get_value(session, "vpa_verifications_per_hour"))
    since = _now() - timedelta(hours=1)
    oldest, used = (
        await session.execute(
            select(func.min(UpiCheck.created_at), func.count()).where(
                UpiCheck.customer_id == context.customer.id, UpiCheck.created_at > since
            )
        )
    ).one()
    if used >= limit:
        retry_after = int((oldest + timedelta(hours=1) - _now()).total_seconds()) if oldest else 3600
        raise AppError(
            "RATE_LIMITED",
            "too many UPI ID checks; please wait and try again",
            status_code=429,
            headers={"Retry-After": str(max(1, retry_after))},
        )
    session.add(UpiCheck(customer_id=context.customer.id, kind=kind))
    await session.commit()


async def _verify(
    session: AsyncSession, context: CustomerContext, bank: PaymentProvider, upi_id: str
) -> VpaVerification:
    await _record_check(session, context, "verify")
    try:
        return await bank.verify_vpa(upi_id)
    except BankError as exc:
        raise _bank_failed(exc) from None


async def _lookup_mobile(
    session: AsyncSession, context: CustomerContext, bank: PaymentProvider
) -> VpaVerification | None:
    mobile = await verified_mobile(session, context.customer.id)
    if mobile is None:
        raise AppError("MOBILE_REQUIRED", "verify a mobile number first", status_code=409)
    await _record_check(session, context, "lookup")
    try:
        return await bank.lookup_mobile(mobile)
    except BankError as exc:
        raise _bank_failed(exc) from None


# ---- Checks ----------------------------------------------------------------------------------------------------


async def _require_ready(session: AsyncSession, context: CustomerContext, *, new_link: bool = False) -> KycProfile:
    if not await get_value(session, "upi_linking"):
        raise AppError("FEATURE_OFF", "linking UPI IDs is paused; please try again later", status_code=503)
    profile = await get_profile(session, context.customer.id)
    if profile.tier == "T0" or not profile.full_name:
        raise AppError("KYC_REQUIRED", "complete minimum KYC before linking a UPI ID", status_code=409)
    cooling_until = context.device.cooling_until
    if new_link and cooling_until is not None and cooling_until > _now():
        raise AppError(
            "DEVICE_COOLING",
            "for your security, a new UPI ID can't be linked from a new device yet; try again later",
            status_code=403,
        )
    return profile


@dataclass(frozen=True)
class Candidate:
    upi_id: str
    holder_name_masked: str
    name_score: float
    found_by_mobile: bool
    problem: tuple[str, str] | None  # (code, message) if it can't be linked

    @property
    def can_link(self) -> bool:
        return self.problem is None


def _assess(found: VpaVerification, kyc_name: str, found_by_mobile: bool, sim_binding: str) -> Candidate:
    problem: tuple[str, str] | None = None
    if not found.active:
        problem = ("UPI_INACTIVE", "this UPI ID isn't active at the bank")
    elif found.currency != "INR":
        problem = ("CURRENCY_NOT_SUPPORTED", "only Indian rupee accounts can be linked")
    elif not names_match(kyc_name, found.account_holder_name):
        problem = ("NAME_MISMATCH", "the name on this bank account doesn't match the name on your KYC")
    elif sim_binding == "enforce" and not found_by_mobile:
        problem = ("NOT_ON_YOUR_MOBILE", "only the UPI ID your bank links to your mobile number can be linked")
    return Candidate(
        upi_id=found.upi_id,
        holder_name_masked=mask_name(found.account_holder_name),
        name_score=match_score(kyc_name, found.account_holder_name),
        found_by_mobile=found_by_mobile,
        problem=problem,
    )


async def _check_upi_id(
    session: AsyncSession, context: CustomerContext, bank: PaymentProvider, upi_id: str, kyc_name: str
) -> Candidate:
    sim_binding = str(await get_value(session, "sim_binding"))
    found = await _verify(session, context, bank, upi_id)
    on_mobile = False
    if sim_binding != "off":
        by_mobile = await _lookup_mobile(session, context, bank)
        on_mobile = by_mobile is not None and by_mobile.upi_id.lower() == upi_id
    return _assess(found, kyc_name, on_mobile, sim_binding)


async def discover(session: AsyncSession, context: CustomerContext) -> Candidate | None:
    """The UPI ID the bank links to the customer's verified mobile number, if any."""
    profile = await _require_ready(session, context)
    bank = await get_payment_provider(session)
    found = await _lookup_mobile(session, context, bank)
    if found is None:
        return None
    sim_binding = str(await get_value(session, "sim_binding"))
    return _assess(found, profile.full_name or "", True, sim_binding)


async def validate(session: AsyncSession, context: CustomerContext, raw_upi_id: str) -> Candidate:
    upi_id = normalise_upi_id(raw_upi_id)
    profile = await _require_ready(session, context)
    bank = await get_payment_provider(session)
    return await _check_upi_id(session, context, bank, upi_id, profile.full_name or "")


# ---- Linking ---------------------------------------------------------------------------------------------------


async def create_link(
    session: AsyncSession, context: CustomerContext, raw_upi_id: str, step_up_token: str | None
) -> UpiLink:
    upi_id = normalise_upi_id(raw_upi_id)
    profile = await _require_ready(session, context, new_link=True)
    await consume_step_up(session, context, step_up_token, "upi_link")
    customer_id = context.customer.id
    index = blind_index("upi_id", upi_id)

    open_links = (
        await session.scalars(select(UpiLink).where(UpiLink.customer_id == customer_id, UpiLink.status.in_(OPEN)))
    ).all()
    if any(link.blind_index == index for link in open_links):
        raise AppError("ALREADY_LINKED", "this UPI ID is already linked or waiting for approval", status_code=409)
    if len(open_links) >= MAX_LINKS:
        raise AppError("LINK_LIMIT", f"you can link up to {MAX_LINKS} UPI IDs; remove one first", status_code=409)
    taken = await session.scalar(
        select(UpiLink.id).where(
            UpiLink.blind_index == index, UpiLink.status == "active", UpiLink.customer_id != customer_id
        )
    )
    if taken:
        raise AppError("UPI_ID_IN_USE", "this UPI ID is linked to another account", status_code=409)

    bank = await get_payment_provider(session)
    candidate = await _check_upi_id(session, context, bank, upi_id, profile.full_name or "")
    if candidate.problem:
        code, message = candidate.problem
        await record(
            session,
            context.actor,
            "upi.link_refused",
            "customer",
            str(customer_id),
            after={"upi_id": mask_upi_id(upi_id), "reason": code, "name_score": candidate.name_score},
        )
        await session.commit()
        raise AppError(code, message, status_code=422)
    sim_binding = await get_value(session, "sim_binding")
    if sim_binding == "monitor" and not candidate.found_by_mobile:
        log.warning(
            "UPI ID not linked to the customer's mobile (sim_binding monitor)", extra={"customer": str(customer_id)}
        )

    now = _now()
    try:
        consent = await bank.request_consent(upi_id, {ConsentPurpose.LINK}, now + timedelta(days=LINK_CONSENT_DAYS))
    except BankError as exc:
        raise _bank_failed(exc) from None

    link = UpiLink(
        id=uuid.uuid4(),
        customer_id=customer_id,
        provider=MOCKBANK,
        blind_index=index,
        upi_id_encrypted="",
        upi_id_masked=mask_upi_id(upi_id),
        holder_name_masked=candidate.holder_name_masked,
        name_match_score=candidate.name_score,
        found_by_mobile=candidate.found_by_mobile,
        consent_id=consent.consent_id,
        consent_expires_at=consent.expires_at,
        status="pending",
        is_default=False,
    )
    link.upi_id_encrypted = get_cipher().encrypt(upi_id, _upi_context(link.id))
    session.add(link)
    await session.flush()
    await record(
        session,
        context.actor,
        "upi.link_requested",
        "upi_link",
        str(link.id),
        after={
            "upi_id": link.upi_id_masked,
            "consent_id": consent.consent_id,
            "name_score": candidate.name_score,
            "found_by_mobile": candidate.found_by_mobile,
            "sim_binding": sim_binding,
        },
    )
    await session.commit()
    return link


async def _alert(session: AsyncSession, customer_id: uuid.UUID, text: str) -> None:
    # The change itself must not fail because an alert couldn't be sent.
    with contextlib.suppress(MessagingUnavailable):
        await send_security_alert(session, await contact_points(session, customer_id), text)


async def _promote_default(session: AsyncSession, customer_id: uuid.UUID) -> None:
    """After the default link ends, the oldest remaining active link becomes the default."""
    next_link = await session.scalar(
        select(UpiLink)
        .where(UpiLink.customer_id == customer_id, UpiLink.status == "active")
        .order_by(UpiLink.activated_at)
        .limit(1)
    )
    if next_link is not None:
        next_link.is_default = True
        await session.flush()


async def _end(session: AsyncSession, link: UpiLink, status: str, reason: str, actor: Actor) -> None:
    was_active, was_default = link.status == "active", link.is_default
    link.status = status
    link.status_reason = reason
    link.is_default = False
    link.ended_at = _now()
    await session.flush()
    if was_default:
        await _promote_default(session, link.customer_id)
    await record(
        session,
        actor,
        "upi.unlinked" if was_active else "upi.link_cancelled" if reason == "customer" else "upi.link_failed",
        "upi_link",
        str(link.id),
        after={"upi_id": link.upi_id_masked, "status": status, "reason": reason},
    )
    if was_active:
        await _alert(session, link.customer_id, f"UPI ID {link.upi_id_masked} was unlinked from your account.")


async def _activate(session: AsyncSession, link: UpiLink, consent: Consent, actor: Actor) -> None:
    elsewhere = await session.scalar(
        select(UpiLink.id).where(
            UpiLink.blind_index == link.blind_index, UpiLink.status == "active", UpiLink.id != link.id
        )
    )
    if elsewhere:
        await _end(session, link, "failed", "linked_elsewhere", actor)
        return
    has_default = await session.scalar(
        select(UpiLink.id).where(UpiLink.customer_id == link.customer_id, UpiLink.is_default)
    )
    link.status = "active"
    link.activated_at = _now()
    link.consent_expires_at = consent.expires_at
    link.is_default = has_default is None  # the first link becomes the default funding source
    await session.flush()
    await record(
        session,
        actor,
        "upi.linked",
        "upi_link",
        str(link.id),
        after={"upi_id": link.upi_id_masked, "consent_id": consent.consent_id, "is_default": link.is_default},
    )
    await _alert(session, link.customer_id, f"UPI ID {link.upi_id_masked} was linked to your account.")


async def apply_consent(session: AsyncSession, link: UpiLink, consent: Consent, actor: Actor) -> None:
    """Bring a link in line with its consent at the bank. Joins the caller's transaction."""
    genuine = (
        consent.consent_id == link.consent_id
        and consent.upi_id.lower() == link_upi_id(link)
        and ConsentPurpose.LINK in consent.purposes
    )
    if not genuine:
        log.error("bank consent does not match its link", extra={"link": str(link.id)})
        if link.status in OPEN:
            await _end(session, link, "failed" if link.status == "pending" else "removed", "consent_mismatch", actor)
        return

    status = consent.status
    if link.status == "pending":
        if status == ConsentStatus.ACTIVE:
            await _activate(session, link, consent, actor)
        elif status != ConsentStatus.PENDING:
            await _end(session, link, "failed", status.value.lower(), actor)
        elif link.created_at + APPROVAL_WINDOW <= _now():
            await _end(session, link, "failed", "approval_timeout", actor)
    elif link.status == "active":
        if status == ConsentStatus.ACTIVE:
            link.consent_expires_at = consent.expires_at
        elif status in (ConsentStatus.REVOKED, ConsentStatus.EXPIRED):
            await _end(session, link, "removed", f"bank_{status.value.lower()}", actor)


async def list_links(session: AsyncSession, context: CustomerContext) -> list[UpiLink]:
    """Open links and recent failures. Pending ones are re-checked with the bank first."""
    customer_id = context.customer.id
    now = _now()
    links = (
        await session.scalars(
            select(UpiLink)
            .where(
                UpiLink.customer_id == customer_id,
                UpiLink.status.in_(OPEN) | ((UpiLink.status == "failed") & (UpiLink.ended_at > now - RECENT_FAILURES)),
            )
            .order_by(UpiLink.created_at)
        )
    ).all()

    pending = [link for link in links if link.status == "pending"]
    bank: PaymentProvider | None = None
    if pending:
        with contextlib.suppress(ProviderUnavailable):
            bank = await get_payment_provider(session)
    for link in pending:
        if bank is None:
            if link.created_at + APPROVAL_WINDOW <= now:
                await _end(session, link, "failed", "approval_timeout", context.actor)
            continue
        try:
            consent = await bank.get_consent(link.consent_id)
        except BankError as exc:
            log.warning("could not re-check a pending UPI link", extra={"link": str(link.id), "code": exc.code})
            continue
        await apply_consent(session, link, consent, context.actor)
    for link in links:
        if link.status == "active" and link.consent_expires_at <= now:
            await _end(session, link, "removed", "consent_expired", context.actor)
    await session.commit()
    return [link for link in links if link.status != "removed"]


async def on_consent_updated(session: AsyncSession, consent_id: str) -> bool:
    """A consent.updated event. The bank is asked for the consent's current state rather than trusting the event
    body (plan 9.4). A consent the app didn't request for a link never creates one (plan 9.1). Returns whether a
    link was affected."""
    link = await session.scalar(select(UpiLink).where(UpiLink.consent_id == consent_id).with_for_update())
    if link is None or link.status not in OPEN:
        return False
    bank = await get_payment_provider(session)
    consent = await bank.get_consent(consent_id)  # BankError propagates: the event is retried later
    await apply_consent(session, link, consent, BANK_ACTOR)
    return True


async def _owned_link(session: AsyncSession, context: CustomerContext, link_id: uuid.UUID) -> UpiLink:
    link = await session.get(UpiLink, link_id, with_for_update=True)
    if link is None or link.customer_id != context.customer.id or link.status not in OPEN:
        raise AppError("NOT_FOUND", "no such UPI link", status_code=404)
    return link


async def set_default(
    session: AsyncSession, context: CustomerContext, link_id: uuid.UUID, step_up_token: str | None
) -> UpiLink:
    await consume_step_up(session, context, step_up_token, "upi_set_default")
    link = await _owned_link(session, context, link_id)
    if link.status != "active":
        raise AppError("NOT_ACTIVE", "this UPI ID is still waiting for approval at the bank", status_code=409)
    if not link.is_default:
        current = await session.scalar(
            select(UpiLink).where(UpiLink.customer_id == context.customer.id, UpiLink.is_default).with_for_update()
        )
        if current is not None:
            current.is_default = False
            await session.flush()
        link.is_default = True
        await session.flush()
        await record(
            session,
            context.actor,
            "upi.default_changed",
            "upi_link",
            str(link.id),
            before={"default": current.upi_id_masked if current else None},
            after={"default": link.upi_id_masked},
        )
    await session.commit()
    return link


async def remove_link(
    session: AsyncSession, context: CustomerContext, link_id: uuid.UUID, step_up_token: str | None
) -> None:
    """Unlink. The bank's consent is left for the customer to revoke on the bank's website; the app stops using
    the UPI ID straight away either way."""
    await consume_step_up(session, context, step_up_token, "upi_unlink")
    link = await _owned_link(session, context, link_id)
    await _end(session, link, "removed", "customer", context.actor)
    await session.commit()
