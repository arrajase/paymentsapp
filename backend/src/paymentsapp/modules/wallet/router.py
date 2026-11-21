"""Customer API: the wallet and its payments (plan M4, M5).

Requests that move money carry an Idempotency-Key header (a UUID the app makes once per attempt): repeating the
request with the same key returns the same payment instead of starting another.
"""

import uuid
from typing import Annotated

from fastapi import APIRouter, Header, Query, status
from sqlalchemy import or_, select

from paymentsapp.api.deps import CurrentCustomer, DbSession
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.registry import get_bank_website
from paymentsapp.modules.compliance.limits import get_limits
from paymentsapp.modules.kyc.service import get_profile
from paymentsapp.modules.payments import service as payments
from paymentsapp.modules.payments.models import Payment
from paymentsapp.modules.upi.name_match import mask_name
from paymentsapp.modules.upi.service import check_payee, record_check
from paymentsapp.modules.wallet import ledger
from paymentsapp.modules.wallet.schemas import (
    AddMoney,
    AddMoneyOut,
    NewPayment,
    PayeeIn,
    PayeeOut,
    PaymentOut,
    RecipientIn,
    RecipientOut,
    WalletOut,
)

router = APIRouter(prefix="/v1", tags=["customer"])

IdempotencyHeader = Annotated[uuid.UUID, Header(alias="Idempotency-Key")]
StepUpHeader = Annotated[str | None, Header(alias="X-Step-Up-Token")]


def payment_out(payment: Payment, viewer: uuid.UUID) -> PaymentOut:
    received = payment.recipient_customer_id == viewer
    return PaymentOut(
        id=payment.id,
        kind=payment.kind,
        direction="in" if payment.kind == "add_money" or received else "out",
        status=payment.status,
        amount=payment.amount,
        currency="INR",
        counterparty=(payment.payer_masked or "") if received else payment.counterparty_masked,
        failure_reason=payment.failure_reason,
        failure_message=payments.failure_message(payment),
        created_at=payment.created_at,
        completed_at=payment.completed_at,
        expires_at=payment.expires_at,
    )


@router.get("/wallet", response_model=WalletOut)
async def wallet(customer: CurrentCustomer, session: DbSession) -> WalletOut:
    profile = await get_profile(session, customer.customer.id)
    tier = get_limits().tiers[profile.tier]
    room = await payments.load_room(session, customer, profile.tier)
    balance = await ledger.wallet_balance(session, customer.customer.id)
    await session.commit()
    return WalletOut(
        balance=balance,
        currency="INR",
        tier=profile.tier,
        enabled=tier.wallet,
        max_balance=tier.max_balance,
        can_add_now=room.allowed,
        can_add_reason=room.reason,
        merchant_payments=tier.merchant_payments,
        p2p_transfers=tier.p2p_transfers,
        withdrawals=tier.withdrawals,
    )


@router.get("/wallet/transactions", response_model=list[PaymentOut])
async def transactions(
    customer: CurrentCustomer, session: DbSession, limit: Annotated[int, Query(ge=1, le=100)] = 50
) -> list[PaymentOut]:
    me = customer.customer.id
    rows = await session.scalars(
        select(Payment)
        .where(or_(Payment.customer_id == me, Payment.recipient_customer_id == me))
        .order_by(Payment.created_at.desc())
        .limit(limit)
    )
    return [payment_out(p, me) for p in rows.all()]


@router.post("/wallet/add-money", response_model=AddMoneyOut, status_code=status.HTTP_201_CREATED)
async def add_money(
    body: AddMoney, customer: CurrentCustomer, session: DbSession, idempotency_key: IdempotencyHeader
) -> AddMoneyOut:
    """Start adding money from a linked UPI ID. Approve it with your UPI PIN on the bank's website, then check
    GET /v1/payments/{id} until it succeeds or fails."""
    payment = await payments.add_money(
        session, customer, amount=body.amount, link_id=body.upi_link_id, idempotency_key=idempotency_key
    )
    out = payment_out(payment, customer.customer.id)
    return AddMoneyOut(**out.model_dump(), approve_at=await get_bank_website(session))


@router.post("/payments", response_model=PaymentOut, status_code=status.HTTP_201_CREATED)
async def pay(
    body: NewPayment,
    customer: CurrentCustomer,
    session: DbSession,
    idempotency_key: IdempotencyHeader,
    step_up: StepUpHeader = None,
) -> PaymentOut:
    """Pay a merchant, withdraw to your bank, or send to another wallet. Needs a step-up token for the "payment"
    action. A bank payment may come back UNKNOWN; check GET /v1/payments/{id} until it settles."""
    if body.kind == "p2p":
        if not body.recipient_mobile:
            raise AppError("RECIPIENT_REQUIRED", "enter the mobile number to send to", status_code=422)
        payment = await payments.transfer(
            session,
            customer,
            amount=body.amount,
            recipient_mobile=body.recipient_mobile,
            idempotency_key=idempotency_key,
            step_up_token=step_up,
        )
    else:
        payment = await payments.pay_out(
            session,
            customer,
            kind=body.kind,
            amount=body.amount,
            payee_upi_id=body.payee_upi_id,
            idempotency_key=idempotency_key,
            step_up_token=step_up,
        )
    return payment_out(payment, customer.customer.id)


@router.post("/payments/payee", response_model=PayeeOut)
async def payee(body: PayeeIn, customer: CurrentCustomer, session: DbSession) -> PayeeOut:
    """Check a UPI ID before paying it. Shows only a masked name, and counts towards the UPI check limit."""
    found = await check_payee(session, customer, body.upi_id)
    return PayeeOut(upi_id=found.upi_id, name_masked=mask_name(found.account_holder_name), active=found.active)


@router.post("/payments/recipient", response_model=RecipientOut)
async def recipient(body: RecipientIn, customer: CurrentCustomer, session: DbSession) -> RecipientOut:
    """Find the wallet customer with this mobile number. Counts towards the same limit as UPI checks, so it can't
    be used to find out who has a wallet."""
    await record_check(session, customer, "recipient")
    found = await payments.find_recipient(session, customer, body.mobile)
    return RecipientOut(mobile_masked=found.mobile_masked, name_masked=found.name_masked, can_receive=found.can_receive)


@router.get("/payments/{payment_id}", response_model=PaymentOut)
async def payment(payment_id: uuid.UUID, customer: CurrentCustomer, session: DbSession) -> PaymentOut:
    return payment_out(await payments.customer_payment(session, customer, payment_id), customer.customer.id)
