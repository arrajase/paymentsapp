"""Posting to the ledger (plan M4). The only code that writes journal entries or changes account balances.

post() checks the entry balances, locks every account it touches in a fixed order (so two postings can't
deadlock), updates the balances and writes the lines, all in the caller's transaction. A wallet that would go below
zero raises InsufficientFunds and nothing is written.
"""

import uuid
from dataclasses import dataclass
from typing import Literal

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.errors import AppError
from paymentsapp.modules.wallet.models import JournalEntry, JournalLine, LedgerAccount

Direction = Literal["debit", "credit"]
AccountKind = Literal["asset", "liability", "income", "expense"]
# Debits increase these; credits increase the rest.
_DEBIT_NORMAL = {"asset", "expense"}


class InsufficientFunds(AppError):
    def __init__(self) -> None:
        super().__init__("INSUFFICIENT_FUNDS", "your wallet balance is too low for this", status_code=422)


class UnbalancedEntry(ValueError):
    """A bug: the lines of an entry don't add up. Never shown to customers."""


@dataclass(frozen=True)
class Line:
    account: LedgerAccount
    direction: Direction
    amount: int


def wallet_code(customer_id: uuid.UUID) -> str:
    return f"wallet:{customer_id}"


def escrow_code(provider: str) -> str:
    return f"escrow:{provider}"


def in_transit_code(provider: str) -> str:
    return f"in_transit:{provider}"


async def account(
    session: AsyncSession,
    code: str,
    kind: AccountKind,
    *,
    customer_id: uuid.UUID | None = None,
    provider: str | None = None,
) -> LedgerAccount:
    """Get an account by code, creating it on first use. Safe when two requests create it at once."""
    await session.execute(
        insert(LedgerAccount)
        .values(id=uuid.uuid4(), code=code, kind=kind, customer_id=customer_id, provider=provider, balance=0)
        .on_conflict_do_nothing(index_elements=[LedgerAccount.code])
    )
    found = await session.scalar(select(LedgerAccount).where(LedgerAccount.code == code))
    assert found is not None and found.kind == kind
    return found


async def wallet_account(session: AsyncSession, customer_id: uuid.UUID) -> LedgerAccount:
    return await account(session, wallet_code(customer_id), "liability", customer_id=customer_id)


async def wallet_balance(session: AsyncSession, customer_id: uuid.UUID) -> int:
    balance = await session.scalar(select(LedgerAccount.balance).where(LedgerAccount.code == wallet_code(customer_id)))
    return balance or 0


def _effect(kind: str, direction: Direction, amount: int) -> int:
    increases = (direction == "debit") == (kind in _DEBIT_NORMAL)
    return amount if increases else -amount


async def post(
    session: AsyncSession,
    *,
    kind: str,
    lines: list[Line],
    description: str,
    payment_id: uuid.UUID | None = None,
    provider: str | None = None,
    plugin_version: int | None = None,
    reverses_entry_id: int | None = None,
) -> JournalEntry:
    if len(lines) < 2 or any(not isinstance(line.amount, int) or line.amount <= 0 for line in lines):
        raise UnbalancedEntry("an entry needs at least two lines of positive whole paise")
    debits = sum(line.amount for line in lines if line.direction == "debit")
    credits = sum(line.amount for line in lines if line.direction == "credit")
    if debits != credits:
        raise UnbalancedEntry(f"debits {debits} != credits {credits}")

    # Lock in id order, then apply. The fresh rows replace whatever the session had cached.
    ids = sorted({line.account.id for line in lines})
    locked = {
        a.id: a
        for a in (
            await session.scalars(
                select(LedgerAccount)
                .where(LedgerAccount.id.in_(ids))
                .order_by(LedgerAccount.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).all()
    }
    # Work out every new balance before changing any, so a refusal leaves nothing half-applied.
    new_balances = {account_id: a.balance for account_id, a in locked.items()}
    for line in lines:
        target = locked[line.account.id]
        new_balances[target.id] += _effect(target.kind, line.direction, line.amount)
    if any(locked[i].customer_id is not None and balance < 0 for i, balance in new_balances.items()):
        raise InsufficientFunds()
    for account_id, balance in new_balances.items():
        locked[account_id].balance = balance

    entry = JournalEntry(
        kind=kind,
        payment_id=payment_id,
        provider=provider,
        plugin_version=plugin_version,
        description=description[:200],
        reverses_entry_id=reverses_entry_id,
    )
    session.add(entry)
    await session.flush()
    session.add_all(
        [
            JournalLine(entry_id=entry.id, account_id=line.account.id, direction=line.direction, amount=line.amount)
            for line in lines
        ]
    )
    try:
        await session.flush()
    except IntegrityError as exc:
        if "wallet_not_negative" in str(exc.orig):
            raise InsufficientFunds() from None
        raise
    return entry
