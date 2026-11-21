"""The ledger: the app's own double-entry books, kept in every plugin mode (plan M4).

- Every movement is a journal entry whose lines debit and credit accounts by equal totals. The database checks
  the totals when the transaction commits, and refuses any change to, or deletion of, an entry once written.
  Mistakes are corrected with a reversing entry.
- Each account keeps its balance, updated in the same transaction as its lines, under a row lock. The balance is
  on the account's normal side: debits minus credits for assets, credits minus debits for liabilities.
- A customer's wallet is a liability (money owed to the customer) and can never go below zero.
- Amounts are whole paise.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base, CreatedAtMixin


class LedgerAccount(CreatedAtMixin, Base):
    """A GL account. Codes: wallet:<customer id>, escrow:<plugin>, in_transit:<plugin>, suspense."""

    __tablename__ = "ledger_accounts"
    __table_args__ = (
        CheckConstraint("kind IN ('asset', 'liability', 'income', 'expense')", name="kind"),
        CheckConstraint("currency = 'INR'", name="currency"),
        CheckConstraint("customer_id IS NULL OR balance >= 0", name="wallet_not_negative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(80), nullable=False, unique=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"), unique=True)
    provider: Mapped[str | None] = mapped_column(String(64))  # plugin key, for escrow and in-transit accounts
    currency: Mapped[str] = mapped_column(String(3), nullable=False, default="INR")
    balance: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class JournalEntry(Base):
    __tablename__ = "journal_entries"
    # One entry of each kind per payment, so settling a payment twice can't post it twice.
    __table_args__ = (UniqueConstraint("payment_id", "kind", name="uq_journal_entries_payment_kind"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "add_money"
    payment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, index=True)
    # The plugin and its settings version the money went through (plan M4).
    provider: Mapped[str | None] = mapped_column(String(64))
    plugin_version: Mapped[int | None] = mapped_column(Integer)
    description: Mapped[str] = mapped_column(String(200), nullable=False)
    reverses_entry_id: Mapped[int | None] = mapped_column(ForeignKey("journal_entries.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


class JournalLine(Base):
    __tablename__ = "journal_lines"
    __table_args__ = (
        CheckConstraint("direction IN ('debit', 'credit')", name="direction"),
        CheckConstraint("amount > 0", name="amount_positive"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    entry_id: Mapped[int] = mapped_column(ForeignKey("journal_entries.id"), nullable=False, index=True)
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("ledger_accounts.id"), nullable=False, index=True)
    direction: Mapped[str] = mapped_column(String(6), nullable=False)
    amount: Mapped[int] = mapped_column(BigInteger, nullable=False)


class ReconciliationRun(Base):
    """One comparison of the ledger with a bank's escrow account for a day (plan M4).

    clean is true only when the balances agree and every escrow statement line and every escrow ledger line has its
    partner. issues lists what didn't match, by client_reference, for operations to investigate.
    """

    __tablename__ = "reconciliation_runs"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    business_date: Mapped[date] = mapped_column(Date, nullable=False)  # IST
    ran_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    ran_by: Mapped[str] = mapped_column(String(64), nullable=False)  # "worker" or an admin ID
    bank_balance: Mapped[int | None] = mapped_column(BigInteger)
    ledger_balance: Mapped[int] = mapped_column(BigInteger, nullable=False)
    matched: Mapped[int] = mapped_column(Integer, nullable=False)
    clean: Mapped[bool] = mapped_column(Boolean, nullable=False)
    issues: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False, default=list)
