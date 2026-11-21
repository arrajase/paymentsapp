"""Payments: one row per money movement, its status history, and the app's idempotency keys (plan M5).

Kinds: add_money (bank to wallet), merchant (wallet to any UPI ID), withdrawal (wallet to the customer's own linked
UPI ID) and p2p (wallet to another customer's wallet, in the ledger only).

Statuses: CREATED -> CHECKED -> AWAITING_AUTH -> SUCCESS | FAILED, with UNKNOWN when the bank's answer was lost.
Payments out skip AWAITING_AUTH: the app's own escrow account pays. A timeout is UNKNOWN, never FAILED: the money
may have moved. Every change of status is kept in payment_events.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    String,
    Text,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base, CreatedAtMixin

FINAL = ("SUCCESS", "FAILED")
OPEN = ("CREATED", "CHECKED", "AWAITING_AUTH", "UNKNOWN")


class Payment(CreatedAtMixin, Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("kind IN ('add_money', 'merchant', 'withdrawal', 'p2p')", name="kind"),
        CheckConstraint(
            "status IN ('CREATED', 'CHECKED', 'AWAITING_AUTH', 'UNKNOWN', 'SUCCESS', 'FAILED')", name="status"
        ),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint("currency = 'INR'", name="currency"),
        # The worker's sweep looks for payments still waiting on the bank.
        Index(
            "ix_payments_open",
            "updated_at",
            postgresql_where=text("status IN ('CHECKED', 'AWAITING_AUTH', 'UNKNOWN')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)  # paise
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    # The bank plugin the money went through, and its settings version; None for p2p, which stays in the ledger.
    provider: Mapped[str | None] = mapped_column(String(64))
    plugin_version: Mapped[int | None] = mapped_column(Integer)
    upi_link_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("upi_links.id"))
    # The other side, encrypted, and masked for display: a UPI ID, or for p2p the recipient's mobile number.
    counterparty_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    counterparty_masked: Mapped[str] = mapped_column(String(80), nullable=False)
    # p2p only: the receiving customer, and the sender as the receiver sees them (masked mobile).
    recipient_customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), index=True)
    payer_masked: Mapped[str | None] = mapped_column(String(80))
    # The app's own account at the bank, e.g. the escrow UPI ID; None for p2p.
    own_upi_id: Mapped[str | None] = mapped_column(String(80))
    # Sent to the bank: the client reference shows on statements, the key makes retries safe (plan 9.4).
    client_reference: Mapped[str] = mapped_column(String(40), nullable=False, unique=True)
    bank_idempotency_key: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    bank_reference: Mapped[str | None] = mapped_column(String(64), unique=True)  # collect ID or transaction ID
    bank_transaction_id: Mapped[str | None] = mapped_column(String(64))
    # The bank's transfer ID, shared by both legs of a movement. The mock bank puts the payment's client_reference
    # only on the payer's leg, so reconciliation finds the escrow leg of money added by this ID.
    bank_transfer_id: Mapped[str | None] = mapped_column(String(64), index=True)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)  # tries to settle an UNKNOWN
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PaymentEvent(Base):
    __tablename__ = "payment_events"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False, index=True)
    from_status: Mapped[str | None] = mapped_column(String(16))
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason: Mapped[str | None] = mapped_column(String(200))
    actor: Mapped[str] = mapped_column(String(64), nullable=False)  # "customer:<id>", "worker", "webhook", ...
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class IdempotencyKey(CreatedAtMixin, Base):
    """The app's Idempotency-Key header: a retried request gets the original payment back; the same key with a
    different request is refused (plan M5)."""

    __tablename__ = "idempotency_keys"

    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), primary_key=True)
    key: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("payments.id"), nullable=False)
