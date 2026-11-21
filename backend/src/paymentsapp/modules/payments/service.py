"""Payments orchestration (plan M5): the only code that moves money.

Adding money:
1. add_money() checks the customer's limits, saves the payment (CHECKED) and only then asks the bank for a collect
   request from the customer's linked UPI ID to the escrow account. The customer approves it with their UPI PIN on
   the bank's website (AWAITING_AUTH).
2. settle() asks the bank where the collect request stands. On SUCCESS it credits the wallet in the ledger (debit
   escrow, credit the customer's wallet), in the same transaction that marks the payment SUCCESS.
3. settle() runs when the app checks the payment, when the bank's collect_request.updated event arrives (webhook
   or poller), and from the worker's sweep, so a payment settles even if the customer closes the app.

Safety rules (plan 9.4):
- The payment is saved before the bank is called, and the bank call always uses the payment's own idempotency key,
  so it can be repeated safely.
- A lost answer makes the payment UNKNOWN, never FAILED: the bank may have created the request. Settling an UNKNOWN
  payment repeats the identical request with the same key; the bank answers from its stored result.
- The wallet is credited only from the bank's own record of the collect request, never from an event body.

Paying out (merchants, and withdrawals to the customer's own linked UPI ID):
1. pay_out() checks limits and the step-up token, then holds the amount in the ledger (debit the wallet, credit
   in-transit) in the same transaction that saves the payment, before the bank is called.
2. The escrow account pays the payee at the bank. On SUCCESS the hold is captured (debit in-transit, credit
   escrow); on a known failure it is released back to the wallet. A lost answer leaves it UNKNOWN and held, and
   settling repeats the same request with the same key.

Wallet-to-wallet transfers (transfer()) stay inside the ledger: debit the sender, credit the receiver, at once.
"""

import contextlib
import hashlib
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.crypto import get_cipher
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.bank_gateway import (
    BankError,
    BankTransaction,
    BankUnavailable,
    CollectRequest,
    CollectStatus,
    OutcomeUnknown,
    PaymentProvider,
    TxnStatus,
)
from paymentsapp.integrations.messaging.base import MessagingUnavailable
from paymentsapp.integrations.registry import bank_plugin, get_payment_provider
from paymentsapp.modules.compliance.limits import financial_year_start, get_limits, month_start, rupees
from paymentsapp.modules.controls.service import get_value
from paymentsapp.modules.identity.identifiers import normalise
from paymentsapp.modules.identity.queries import contact_points, customer_by_mobile, masked_mobile
from paymentsapp.modules.identity.sessions import CustomerContext
from paymentsapp.modules.identity.step_up import consume_step_up
from paymentsapp.modules.kyc.service import get_profile
from paymentsapp.modules.notifications.service import send_transaction_alert
from paymentsapp.modules.payments.models import FINAL, IdempotencyKey, Payment, PaymentEvent
from paymentsapp.modules.upi.models import UpiLink
from paymentsapp.modules.upi.name_match import mask_name
from paymentsapp.modules.upi.service import link_upi_id, normalise_upi_id
from paymentsapp.modules.wallet import ledger
from paymentsapp.modules.wallet.models import LedgerAccount

log = logging.getLogger(__name__)

COLLECT_EXPIRY_SECONDS = 600  # the bank allows at most 900
COLLECT_NOTE = "Add money to your paymentsapp wallet"
SWEEP_AFTER = timedelta(seconds=20)  # the sweep leaves a payment alone this long after it last changed
MAX_BACKOFF = timedelta(minutes=10)
STUCK_CHECKED = timedelta(minutes=2)  # a CHECKED payment this old lost its process before the bank call

FAILURE_MESSAGES = {
    "DECLINED": "You declined the request at your bank.",
    "EXPIRED": "The request expired before it was approved.",
    "INSUFFICIENT_FUNDS": "Your bank account didn't have enough money.",
    "BANK_UNAVAILABLE": "The bank isn't responding, so nothing was taken. Try again in a minute.",
    "UPI_INACTIVE": "This UPI ID isn't active at the bank.",
    "UPI_NOT_FOUND": "No bank account has this UPI ID. Nothing was taken.",
    "CURRENCY_MISMATCH": "This account can't receive Indian rupees. Nothing was taken.",
}


def _now() -> datetime:
    return datetime.now(UTC)


def _counterparty_context(payment_id: uuid.UUID) -> str:
    return f"payments.counterparty:{payment_id}"


def failure_message(payment: Payment) -> str | None:
    if payment.status != "FAILED":
        return None
    return FAILURE_MESSAGES.get(payment.failure_reason or "", "The payment didn't go through. Nothing was taken.")


def request_hash(**fields: object) -> str:
    """A fingerprint of a request body, to tell a retry from a different request under the same key."""
    return hashlib.sha256(json.dumps(fields, sort_keys=True, default=str).encode()).hexdigest()


async def _move(session: AsyncSession, payment: Payment, to: str, actor: str, reason: str | None = None) -> None:
    session.add(
        PaymentEvent(payment_id=payment.id, from_status=payment.status, to_status=to, reason=reason, actor=actor)
    )
    payment.status = to
    payment.updated_at = _now()
    if to in FINAL:
        payment.completed_at = payment.updated_at
    if to == "FAILED":
        payment.failure_reason = (reason or "FAILED")[:64]
    await session.flush()


# ---- Limits ----------------------------------------------------------------------------------------------------


async def _loaded_since(session: AsyncSession, customer_id: uuid.UUID, since: datetime) -> int:
    """Money added since a date, counting requests still waiting on the bank (they may yet succeed)."""
    total = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.customer_id == customer_id,
            Payment.kind == "add_money",
            Payment.status != "FAILED",
            Payment.created_at >= since,
        )
    )
    return int(total or 0)


async def _pending_loads(session: AsyncSession, customer_id: uuid.UUID) -> int:
    total = await session.scalar(
        select(func.coalesce(func.sum(Payment.amount), 0)).where(
            Payment.customer_id == customer_id,
            Payment.kind == "add_money",
            Payment.status.not_in(FINAL),
        )
    )
    return int(total or 0)


@dataclass(frozen=True)
class LoadRoom:
    """How much the customer can add right now, and the limit that sets it."""

    allowed: int
    reason: str


async def load_room(session: AsyncSession, context: CustomerContext, tier: str) -> LoadRoom:
    limits = get_limits()
    tier_limits = limits.tiers[tier]
    if not tier_limits.wallet:
        return LoadRoom(0, "complete minimum KYC to use the wallet")
    customer_id = context.customer.id
    now = _now()
    assert tier_limits.max_balance is not None
    held = await ledger.wallet_balance(session, customer_id) + await _pending_loads(session, customer_id)
    options = [
        LoadRoom(tier_limits.max_balance - held, f"your wallet can hold up to {rupees(tier_limits.max_balance)}"),
        LoadRoom(limits.max_per_transaction, f"you can add up to {rupees(limits.max_per_transaction)} at a time"),
    ]
    if tier_limits.max_load_per_month is not None:
        used = await _loaded_since(session, customer_id, month_start(now))
        options.append(
            LoadRoom(
                tier_limits.max_load_per_month - used,
                f"you can add up to {rupees(tier_limits.max_load_per_month)} a month with minimum KYC",
            )
        )
    if tier_limits.max_load_per_year is not None:
        used = await _loaded_since(session, customer_id, financial_year_start(now))
        options.append(
            LoadRoom(
                tier_limits.max_load_per_year - used,
                f"you can add up to {rupees(tier_limits.max_load_per_year)} a financial year with minimum KYC",
            )
        )
    cooling_until = context.device.cooling_until
    if cooling_until is not None and cooling_until > now:
        options.append(
            LoadRoom(
                limits.new_device_max_per_transaction,
                f"on a new phone you can add up to {rupees(limits.new_device_max_per_transaction)} at a time",
            )
        )
    tightest = min(options, key=lambda option: option.allowed)
    return LoadRoom(max(0, tightest.allowed), tightest.reason)


# ---- Adding money ----------------------------------------------------------------------------------------------


async def _source_link(session: AsyncSession, customer_id: uuid.UUID, link_id: uuid.UUID | None) -> UpiLink:
    query = select(UpiLink).where(UpiLink.customer_id == customer_id, UpiLink.status == "active")
    query = query.where(UpiLink.id == link_id) if link_id else query.where(UpiLink.is_default)
    link = await session.scalar(query)
    if link is None:
        raise AppError("NO_LINKED_UPI", "link a UPI ID first, to add money from it", status_code=409)
    return link


async def add_money(
    session: AsyncSession,
    context: CustomerContext,
    *,
    amount: int,
    link_id: uuid.UUID | None,
    idempotency_key: uuid.UUID,
) -> Payment:
    customer_id = context.customer.id
    fingerprint = request_hash(kind="add_money", amount=amount, link_id=link_id)
    replay = await _replay(session, customer_id, idempotency_key, fingerprint)
    if replay is not None:
        return replay

    if not await get_value(session, "wallet"):
        raise AppError("FEATURE_OFF", "the wallet is paused; please try again later", status_code=503)
    profile = await get_profile(session, customer_id)
    limits = get_limits()
    if not limits.tiers[profile.tier].wallet:
        raise AppError("KYC_REQUIRED", "complete minimum KYC to use the wallet", status_code=409)
    if amount < limits.min_per_transaction:
        raise AppError("AMOUNT_TOO_SMALL", f"add at least {rupees(limits.min_per_transaction)}", status_code=422)

    # One add-money at a time per customer while the limits are checked, so two can't both squeeze under a cap.
    wallet = await ledger.wallet_account(session, customer_id)
    await session.execute(select(LedgerAccount.id).where(LedgerAccount.id == wallet.id).with_for_update())
    room = await load_room(session, context, profile.tier)
    if amount > room.allowed:
        if room.allowed <= 0:
            raise AppError("LIMIT_REACHED", f"you can't add money right now: {room.reason}", status_code=422)
        raise AppError(
            "LIMIT_EXCEEDED", f"you can add up to {rupees(room.allowed)} now: {room.reason}", status_code=422
        )

    link = await _source_link(session, customer_id, link_id)
    plugin = await bank_plugin(session)
    escrow = plugin.config.get("escrow_upi_id")
    if not escrow:
        log.error("the bank plugin has no escrow_upi_id")
        raise AppError("SERVICE_UNAVAILABLE", "adding money is temporarily unavailable", status_code=503)

    payment = Payment(
        id=uuid.uuid4(),
        customer_id=customer_id,
        kind="add_money",
        status="CREATED",
        amount=amount,
        provider=plugin.key,
        plugin_version=plugin.version,
        upi_link_id=link.id,
        counterparty_encrypted="",
        counterparty_masked=link.upi_id_masked,
        own_upi_id=escrow,
        client_reference="",
        bank_idempotency_key=uuid.uuid4(),
    )
    payment.client_reference = f"pa-{payment.id.hex}"
    payment.counterparty_encrypted = get_cipher().encrypt(link_upi_id(link), _counterparty_context(payment.id))
    session.add(payment)
    await session.flush()  # before its key: there's no ORM relationship to order the two inserts
    session.add(
        IdempotencyKey(customer_id=customer_id, key=idempotency_key, request_hash=fingerprint, payment_id=payment.id)
    )
    try:
        await session.flush()
    except IntegrityError:
        # The same key arrived twice at once; the other request created the payment.
        await session.rollback()
        replay = await _replay(session, customer_id, idempotency_key, fingerprint)
        if replay is None:
            raise
        return replay
    await _move(session, payment, "CHECKED", f"customer:{customer_id}")
    await session.commit()  # saved before the bank is called, so a crash can't lose track of it

    bank = await get_payment_provider(session)
    await _submit_collect(session, bank, payment, actor=f"customer:{customer_id}", first_try=True)
    await session.commit()
    return payment


async def _replay(session: AsyncSession, customer_id: uuid.UUID, key: uuid.UUID, fingerprint: str) -> Payment | None:
    stored = await session.get(IdempotencyKey, (customer_id, key))
    if stored is None:
        return None
    if stored.request_hash != fingerprint:
        raise AppError(
            "IDEMPOTENCY_KEY_REUSED", "this request key was already used for a different request", status_code=409
        )
    return await session.get(Payment, stored.payment_id)


async def _submit_collect(
    session: AsyncSession, bank: PaymentProvider, payment: Payment, *, actor: str, first_try: bool
) -> None:
    """Ask the bank for the collect request. Repeating this is safe: same key, same body."""
    payer = get_cipher().decrypt(payment.counterparty_encrypted, _counterparty_context(payment.id))
    assert payment.own_upi_id is not None
    try:
        collect = await bank.create_collect(
            payer_upi_id=payer,
            payee_upi_id=payment.own_upi_id,
            amount_paise=payment.amount,
            client_reference=payment.client_reference,
            idempotency_key=str(payment.bank_idempotency_key),
            note=COLLECT_NOTE,
            expires_in_seconds=COLLECT_EXPIRY_SECONDS,
        )
    except OutcomeUnknown:
        if payment.status != "UNKNOWN":
            await _move(session, payment, "UNKNOWN", actor, "the bank's answer was lost")
        else:
            payment.updated_at = _now()
        return
    except BankUnavailable:
        # Nothing was sent this time (the circuit is open). Before any earlier attempt, that means nothing happened.
        if first_try:
            await _move(session, payment, "FAILED", actor, "BANK_UNAVAILABLE")
        else:
            payment.updated_at = _now()
        return
    except BankError as exc:
        await _move(session, payment, "FAILED", actor, exc.code)
        return
    payment.bank_reference = collect.collect_id
    payment.expires_at = collect.expires_at
    await _move(session, payment, "AWAITING_AUTH", actor)
    await _apply_collect(session, bank, payment, collect, actor)


# ---- Settling ------------------------------------------------------------------------------------------------


async def transfer_of(bank: PaymentProvider, transaction_id: str | None) -> str | None:
    """The bank transfer a transaction belongs to, or None if the bank can't say right now. Reconciliation needs it
    to pair the escrow leg of money added, which carries no client_reference at the mock bank."""
    if not transaction_id:
        return None
    try:
        return (await bank.get_transaction(transaction_id)).transfer_id
    except BankError:
        return None


async def _apply_collect(
    session: AsyncSession, bank: PaymentProvider, payment: Payment, collect: CollectRequest, actor: str
) -> None:
    assert payment.provider is not None and payment.own_upi_id is not None
    if (
        collect.client_reference != payment.client_reference
        or collect.amount_paise != payment.amount
        or collect.payee_upi_id.lower() != payment.own_upi_id.lower()
    ):
        # Never credit on a record that doesn't match; reconciliation will surface the money at the bank.
        log.error("bank collect request does not match its payment", extra={"payment": str(payment.id)})
        return
    if collect.status == CollectStatus.PENDING:
        payment.updated_at = _now()
        return
    if collect.status == CollectStatus.SUCCESS:
        escrow = await ledger.account(session, ledger.escrow_code(payment.provider), "asset", provider=payment.provider)
        wallet = await ledger.wallet_account(session, payment.customer_id)
        await ledger.post(
            session,
            kind="add_money",
            lines=[ledger.Line(escrow, "debit", payment.amount), ledger.Line(wallet, "credit", payment.amount)],
            description=f"Added from {payment.counterparty_masked}",
            payment_id=payment.id,
            provider=payment.provider,
            plugin_version=payment.plugin_version,
        )
        payment.bank_transaction_id = collect.transaction_id
        payment.bank_transfer_id = await transfer_of(bank, collect.transaction_id)
        await _move(session, payment, "SUCCESS", actor)
        await _notify(session, payment.customer_id, f"{rupees(payment.amount)} added to your wallet.")
        return
    reason = (
        collect.failure_reason
        if collect.status == CollectStatus.FAILED and collect.failure_reason
        else collect.status.value
    )
    await _move(session, payment, "FAILED", actor, reason)


async def settle(session: AsyncSession, payment_id: uuid.UUID, actor: str) -> Payment:
    """Bring an open payment up to date with the bank. Joins the caller's transaction; the caller commits."""
    payment = await session.get(Payment, payment_id, with_for_update=True, populate_existing=True)
    assert payment is not None
    if payment.status in FINAL or payment.status == "CREATED" or payment.kind == "p2p":
        return payment
    bank = await get_payment_provider(session)
    if payment.kind in ("merchant", "withdrawal"):
        if payment.status in ("CHECKED", "UNKNOWN"):
            payment.attempts += 1
            await _send_pay(session, bank, payment, actor=actor, first_try=False)
        return payment
    if payment.status in ("CHECKED", "UNKNOWN"):
        payment.attempts += 1
        await _submit_collect(session, bank, payment, actor=actor, first_try=False)
        return payment
    assert payment.bank_reference is not None
    try:
        collect = await bank.get_collect(payment.bank_reference)
    except BankError as exc:
        log.warning("could not check a collect request", extra={"payment": str(payment.id), "code": exc.code})
        payment.updated_at = _now()
        return payment
    await _apply_collect(session, bank, payment, collect, actor)
    return payment


async def customer_payment(session: AsyncSession, context: CustomerContext, payment_id: uuid.UUID) -> Payment:
    """One of the customer's payments, settled with the bank first if it is still open."""
    payment = await session.get(Payment, payment_id)
    viewer = context.customer.id
    if payment is None or viewer not in (payment.customer_id, payment.recipient_customer_id):
        raise AppError("NOT_FOUND", "no such payment", status_code=404)
    if payment.status in ("AWAITING_AUTH", "UNKNOWN") and payment.customer_id == viewer:
        with contextlib.suppress(AppError):  # the bank being away mustn't stop the customer seeing the payment
            payment = await settle(session, payment.id, f"customer:{context.customer.id}")
    await session.commit()
    return payment


async def on_collect_updated(session: AsyncSession, collect_id: str) -> bool:
    """A collect_request.updated event. The bank's record is read again rather than trusted from the event."""
    payment_id = await session.scalar(select(Payment.id).where(Payment.bank_reference == collect_id))
    if payment_id is None:
        return False
    await settle(session, payment_id, "bank-event")
    return True


def _due(payment: Payment, now: datetime) -> bool:
    if payment.status == "CHECKED":
        return payment.updated_at <= now - STUCK_CHECKED
    backoff = min(MAX_BACKOFF, SWEEP_AFTER * (1 << min(payment.attempts, 10)))  # doubles with each attempt
    return payment.updated_at <= now - backoff


async def sweep(session: AsyncSession, limit: int = 50) -> int:
    """Settle open payments nobody has looked at lately. For the worker; commits each payment on its own."""
    now = _now()
    candidates = (
        await session.scalars(
            select(Payment)
            .where(Payment.status.in_(("CHECKED", "AWAITING_AUTH", "UNKNOWN")), Payment.updated_at <= now - SWEEP_AFTER)
            .order_by(Payment.updated_at)
            .limit(limit)
        )
    ).all()
    settled = 0
    for payment_id in [p.id for p in candidates if _due(p, now)]:
        try:
            await settle(session, payment_id, "worker")
            await session.commit()
            settled += 1
        except Exception:
            await session.rollback()
            log.exception("could not settle a payment", extra={"payment": str(payment_id)})
    return settled


# ---- Shared by payments out --------------------------------------------------------------------------------------


async def _notify(session: AsyncSession, customer_id: uuid.UUID, text: str) -> None:
    # A payment must not fail, or be rolled back, because its alert couldn't be sent.
    with contextlib.suppress(MessagingUnavailable):
        await send_transaction_alert(session, await contact_points(session, customer_id), text)


async def _check_spend(session: AsyncSession, context: CustomerContext, kind: str, amount: int) -> None:
    """Feature switches, the customer's tier, and the per-payment limits, for money leaving a wallet."""
    if not await get_value(session, "wallet"):
        raise AppError("FEATURE_OFF", "the wallet is paused; please try again later", status_code=503)
    switch = {"p2p": "p2p_transfers", "withdrawal": "withdrawals"}.get(kind)
    if switch and not await get_value(session, switch):
        raise AppError("FEATURE_OFF", "this is paused; please try again later", status_code=503)
    profile = await get_profile(session, context.customer.id)
    limits = get_limits()
    tier = limits.tiers[profile.tier]
    if not tier.wallet:
        raise AppError("KYC_REQUIRED", "complete minimum KYC to use the wallet", status_code=409)
    allowed = {"merchant": tier.merchant_payments, "withdrawal": tier.withdrawals, "p2p": tier.p2p_transfers}[kind]
    if not allowed:
        raise AppError("FULL_KYC_REQUIRED", "complete full KYC to do this", status_code=409)
    if amount < limits.min_per_transaction:
        raise AppError("AMOUNT_TOO_SMALL", f"the smallest payment is {rupees(limits.min_per_transaction)}", 422)
    if amount > limits.max_per_transaction:
        raise AppError("LIMIT_EXCEEDED", f"you can pay up to {rupees(limits.max_per_transaction)} at a time", 422)
    cooling_until = context.device.cooling_until
    if cooling_until is not None and cooling_until > _now() and amount > limits.new_device_max_per_transaction:
        raise AppError(
            "LIMIT_EXCEEDED",
            f"on a new phone you can pay up to {rupees(limits.new_device_max_per_transaction)} at a time",
            status_code=422,
        )


async def _save_new(
    session: AsyncSession, payment: Payment, customer_id: uuid.UUID, key: uuid.UUID, fingerprint: str
) -> Payment | None:
    """Insert the payment and its idempotency key. Returns the other payment if the same key won a race."""
    session.add(payment)
    await session.flush()  # before its key: there's no ORM relationship to order the two inserts
    session.add(IdempotencyKey(customer_id=customer_id, key=key, request_hash=fingerprint, payment_id=payment.id))
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        replay = await _replay(session, customer_id, key, fingerprint)
        if replay is None:
            raise
        return replay
    return None


# ---- Paying out: merchants and withdrawals ---------------------------------------------------------------------

NARRATION = {"merchant": "paymentsapp wallet payment", "withdrawal": "paymentsapp wallet withdrawal"}


async def pay_out(
    session: AsyncSession,
    context: CustomerContext,
    *,
    kind: str,
    amount: int,
    payee_upi_id: str | None,
    idempotency_key: uuid.UUID,
    step_up_token: str | None,
) -> Payment:
    """Pay a merchant's UPI ID, or withdraw to the customer's default linked UPI ID (payee_upi_id is ignored)."""
    assert kind in ("merchant", "withdrawal")
    customer_id = context.customer.id
    fingerprint = request_hash(kind=kind, amount=amount, payee=payee_upi_id)
    # A retry gets its payment back before the step-up check: its token was used up by the first attempt.
    replay = await _replay(session, customer_id, idempotency_key, fingerprint)
    if replay is not None:
        return replay
    await consume_step_up(session, context, step_up_token, "payment")
    await _check_spend(session, context, kind, amount)

    plugin = await bank_plugin(session)
    escrow = plugin.config.get("escrow_upi_id")
    if not escrow:
        log.error("the bank plugin has no escrow_upi_id")
        raise AppError("SERVICE_UNAVAILABLE", "payments are temporarily unavailable", status_code=503)
    if kind == "withdrawal":
        link = await _source_link(session, customer_id, None)
        payee, masked = link_upi_id(link), link.upi_id_masked
    else:
        if not payee_upi_id:
            raise AppError("PAYEE_REQUIRED", "enter the UPI ID to pay", status_code=422)
        payee = normalise_upi_id(payee_upi_id)
        own = {str(v).lower() for k, v in plugin.config.items() if k.endswith("_upi_id")}
        if payee in own:
            raise AppError("INVALID_PAYEE", "this UPI ID can't be paid from the wallet", status_code=422)
        masked = payee  # a merchant's UPI ID isn't the customer's personal data; show it in full

    payment = Payment(
        id=uuid.uuid4(),
        customer_id=customer_id,
        kind=kind,
        status="CREATED",
        amount=amount,
        provider=plugin.key,
        plugin_version=plugin.version,
        counterparty_encrypted="",
        counterparty_masked=masked,
        own_upi_id=escrow,
        client_reference="",
        bank_idempotency_key=uuid.uuid4(),
    )
    payment.client_reference = f"pa-{payment.id.hex}"
    payment.counterparty_encrypted = get_cipher().encrypt(payee, _counterparty_context(payment.id))
    won = await _save_new(session, payment, customer_id, idempotency_key, fingerprint)
    if won is not None:
        return won
    # Hold the money in the same transaction that saves the payment: a balance too low refuses both.
    wallet = await ledger.wallet_account(session, customer_id)
    in_transit = await ledger.account(session, ledger.in_transit_code(plugin.key), "liability", provider=plugin.key)
    try:
        await ledger.post(
            session,
            kind="hold",
            lines=[ledger.Line(wallet, "debit", amount), ledger.Line(in_transit, "credit", amount)],
            description=f"Held for payment to {masked}",
            payment_id=payment.id,
            provider=plugin.key,
            plugin_version=plugin.version,
        )
    except ledger.InsufficientFunds:
        await session.rollback()  # no payment, no key: the customer can try again with a smaller amount
        raise
    await _move(session, payment, "CHECKED", f"customer:{customer_id}")
    await session.commit()  # held and saved before the bank is called

    bank = await get_payment_provider(session)
    await _send_pay(session, bank, payment, actor=f"customer:{customer_id}", first_try=True)
    await session.commit()
    return payment


async def _send_pay(
    session: AsyncSession, bank: PaymentProvider, payment: Payment, *, actor: str, first_try: bool
) -> None:
    """Pay from escrow. Repeating this is safe: same key, same body, and the bank answers from its stored result."""
    assert payment.own_upi_id is not None
    payee = get_cipher().decrypt(payment.counterparty_encrypted, _counterparty_context(payment.id))
    try:
        txn = await bank.pay(
            from_upi_id=payment.own_upi_id,
            to_upi_id=payee,
            amount_paise=payment.amount,
            client_reference=payment.client_reference,
            idempotency_key=str(payment.bank_idempotency_key),
            narration=NARRATION[payment.kind],
        )
    except OutcomeUnknown:
        if payment.status != "UNKNOWN":
            await _move(session, payment, "UNKNOWN", actor, "the bank's answer was lost")
        else:
            payment.updated_at = _now()
        return
    except BankUnavailable:
        if first_try:  # nothing was ever sent
            await _release(session, payment, actor, "BANK_UNAVAILABLE")
        else:
            payment.updated_at = _now()
        return
    except BankError as exc:
        await _release(session, payment, actor, exc.code)
        return
    await _apply_transaction(session, payment, txn, actor)


async def _apply_transaction(session: AsyncSession, payment: Payment, txn: BankTransaction, actor: str) -> None:
    if txn.client_reference != payment.client_reference or txn.amount_paise != payment.amount:
        # Never capture or release on a record that doesn't match; it stays held for reconciliation to explain.
        log.error("bank transaction does not match its payment", extra={"payment": str(payment.id)})
        return
    payment.bank_reference = txn.transaction_id
    if txn.status == TxnStatus.SUCCESS:
        assert payment.provider is not None
        in_transit = await ledger.account(
            session, ledger.in_transit_code(payment.provider), "liability", provider=payment.provider
        )
        escrow = await ledger.account(session, ledger.escrow_code(payment.provider), "asset", provider=payment.provider)
        await ledger.post(
            session,
            kind="capture",
            lines=[ledger.Line(in_transit, "debit", payment.amount), ledger.Line(escrow, "credit", payment.amount)],
            description=f"Paid to {payment.counterparty_masked}",
            payment_id=payment.id,
            provider=payment.provider,
            plugin_version=payment.plugin_version,
        )
        payment.bank_transaction_id = txn.transaction_id
        payment.bank_transfer_id = txn.transfer_id
        await _move(session, payment, "SUCCESS", actor)
        verb = "withdrawn to" if payment.kind == "withdrawal" else "paid to"
        await _notify(session, payment.customer_id, f"{rupees(payment.amount)} {verb} {payment.counterparty_masked}.")
        return
    await _release(session, payment, actor, txn.failure_reason or "FAILED")


async def _release(session: AsyncSession, payment: Payment, actor: str, reason: str) -> None:
    """The payment didn't happen: give the held money back to the wallet."""
    assert payment.provider is not None
    in_transit = await ledger.account(
        session, ledger.in_transit_code(payment.provider), "liability", provider=payment.provider
    )
    wallet = await ledger.wallet_account(session, payment.customer_id)
    await ledger.post(
        session,
        kind="release",
        lines=[ledger.Line(in_transit, "debit", payment.amount), ledger.Line(wallet, "credit", payment.amount)],
        description=f"Returned: payment to {payment.counterparty_masked} didn't go through",
        payment_id=payment.id,
        provider=payment.provider,
        plugin_version=payment.plugin_version,
    )
    await _move(session, payment, "FAILED", actor, reason)


# ---- Wallet-to-wallet transfers --------------------------------------------------------------------------------


@dataclass(frozen=True)
class Recipient:
    customer_id: uuid.UUID
    mobile_masked: str
    name_masked: str
    can_receive: bool


async def find_recipient(session: AsyncSession, context: CustomerContext, raw_mobile: str) -> Recipient:
    """The wallet customer with this verified mobile number, shown masked so the sender can confirm."""
    try:
        mobile = normalise("mobile", raw_mobile).value
    except ValueError as exc:
        raise AppError("INVALID_MOBILE", str(exc), status_code=422) from None
    found = await customer_by_mobile(session, mobile)
    if found is None:
        raise AppError("RECIPIENT_NOT_FOUND", "nobody with a paymentsapp wallet has this number", status_code=404)
    customer_id, masked = found
    if customer_id == context.customer.id:
        raise AppError("INVALID_RECIPIENT", "you can't send money to yourself", status_code=422)
    profile = await get_profile(session, customer_id)
    name = mask_name(profile.full_name) if profile.full_name else "name not given yet"
    return Recipient(customer_id, masked, name, get_limits().tiers[profile.tier].p2p_transfers)


async def transfer(
    session: AsyncSession,
    context: CustomerContext,
    *,
    amount: int,
    recipient_mobile: str,
    idempotency_key: uuid.UUID,
    step_up_token: str | None,
) -> Payment:
    customer_id = context.customer.id
    fingerprint = request_hash(kind="p2p", amount=amount, recipient=recipient_mobile)
    replay = await _replay(session, customer_id, idempotency_key, fingerprint)
    if replay is not None:
        return replay
    await consume_step_up(session, context, step_up_token, "payment")
    await _check_spend(session, context, "p2p", amount)
    recipient = await find_recipient(session, context, recipient_mobile)
    if not recipient.can_receive:
        raise AppError("RECIPIENT_CANT_RECEIVE", "this person needs full KYC to receive wallet transfers", 409)

    limits = get_limits()
    sender_tier = limits.tiers[(await get_profile(session, customer_id)).tier]
    sender_wallet = await ledger.wallet_account(session, customer_id)
    # Lock the sender's wallet while the monthly limit is checked, so two transfers can't both slip under it.
    await session.execute(select(LedgerAccount.id).where(LedgerAccount.id == sender_wallet.id).with_for_update())
    if sender_tier.max_p2p_per_month is not None:
        sent = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0)).where(
                Payment.customer_id == customer_id,
                Payment.kind == "p2p",
                Payment.status == "SUCCESS",
                Payment.created_at >= month_start(_now()),
            )
        )
        room = sender_tier.max_p2p_per_month - int(sent or 0)
        if amount > room:
            raise AppError(
                "LIMIT_EXCEEDED",
                f"you can send up to {rupees(max(0, room))} more this month "
                f"(the limit is {rupees(sender_tier.max_p2p_per_month)} a month)",
                status_code=422,
            )
    recipient_tier = limits.tiers[(await get_profile(session, recipient.customer_id)).tier]
    recipient_held = await ledger.wallet_balance(session, recipient.customer_id) + await _pending_loads(
        session, recipient.customer_id
    )
    if amount > (recipient_tier.max_balance or 0) - recipient_held:
        raise AppError("RECIPIENT_LIMIT", "this would take the recipient's wallet over its limit", status_code=422)

    mobile = normalise("mobile", recipient_mobile).value
    payment = Payment(
        id=uuid.uuid4(),
        customer_id=customer_id,
        kind="p2p",
        status="CREATED",
        amount=amount,
        counterparty_encrypted="",
        counterparty_masked=recipient.mobile_masked,
        recipient_customer_id=recipient.customer_id,
        payer_masked=await masked_mobile(session, customer_id) or "a paymentsapp customer",
        client_reference="",
        bank_idempotency_key=uuid.uuid4(),
    )
    payment.client_reference = f"pa-{payment.id.hex}"
    payment.counterparty_encrypted = get_cipher().encrypt(mobile, _counterparty_context(payment.id))
    won = await _save_new(session, payment, customer_id, idempotency_key, fingerprint)
    if won is not None:
        return won
    recipient_wallet = await ledger.wallet_account(session, recipient.customer_id)
    try:
        await ledger.post(
            session,
            kind="p2p",
            lines=[ledger.Line(sender_wallet, "debit", amount), ledger.Line(recipient_wallet, "credit", amount)],
            description=f"Sent to {recipient.mobile_masked}",
            payment_id=payment.id,
        )
    except ledger.InsufficientFunds:
        await session.rollback()
        raise
    actor = f"customer:{customer_id}"
    await _move(session, payment, "CHECKED", actor)
    await _move(session, payment, "SUCCESS", actor)
    await _notify(session, customer_id, f"{rupees(amount)} sent to {recipient.mobile_masked}.")
    await _notify(session, recipient.customer_id, f"{rupees(amount)} received from {payment.payer_masked}.")
    await session.commit()
    return payment
