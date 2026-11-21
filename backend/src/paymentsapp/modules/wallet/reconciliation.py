"""Reconciliation: the ledger against the bank's escrow account, for one business day in IST (plan M4).

Checks:
- Inside the ledger, escrow equals every wallet plus the money in transit: all money held is owed to someone.
- The escrow ledger balance equals the bank's balance for the escrow UPI ID right now.
- Every line on the bank's escrow statement for the day has a matching ledger line, and every ledger line on the
  escrow account for the day has a matching statement line. They match on client_reference, amount and direction
  (money into escrow is a debit in the ledger and a credit on the bank statement).

A movement settled just after midnight can land on different days on each side, so partners are looked for a day
either side. The mock bank puts a payment's client_reference only on the payer's leg, so the escrow leg of money
added carries none; it is paired through the transfer ID both legs share, kept on the payment. What doesn't match
is listed as an issue for operations; the run is clean only with no issues.
"""

import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.integrations.bank_gateway import BankError, BankTransaction, Direction, IssuerProvider
from paymentsapp.integrations.registry import bank_plugin, get_issuer, get_payment_provider
from paymentsapp.modules.compliance.limits import IST
from paymentsapp.modules.payments.models import Payment
from paymentsapp.modules.payments.service import transfer_of
from paymentsapp.modules.wallet import ledger
from paymentsapp.modules.wallet.models import JournalEntry, JournalLine, LedgerAccount, ReconciliationRun

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Line:
    reference: str | None
    direction: str  # as the bank sees it: "CREDIT" (into escrow) or "DEBIT" (out of escrow)
    amount: int
    day: date
    detail: str


def _day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, datetime.min.time(), tzinfo=IST)
    return start, start + timedelta(days=1)


async def _statement(bank: IssuerProvider, upi_id: str, first: date, last: date) -> list[BankTransaction]:
    lines: list[BankTransaction] = []
    cursor: str | None = None
    while True:
        page = await bank.fetch_statement(upi_id, date_from=first, date_to=last, cursor=cursor, limit=500)
        lines.extend(page.items)
        if not page.next_cursor:
            return lines
        cursor = page.next_cursor


async def _ledger_lines(session: AsyncSession, account: LedgerAccount, first: date, last: date) -> list[_Line]:
    start, _ = _day_bounds(first)
    _, end = _day_bounds(last)
    rows = await session.execute(
        select(
            JournalLine.direction,
            JournalLine.amount,
            JournalEntry.created_at,
            JournalEntry.kind,
            Payment.client_reference,
        )
        .join(JournalEntry, JournalEntry.id == JournalLine.entry_id)
        .outerjoin(Payment, Payment.id == JournalEntry.payment_id)
        .where(JournalLine.account_id == account.id, JournalEntry.created_at >= start, JournalEntry.created_at < end)
    )
    return [
        _Line(
            reference=reference,
            direction="CREDIT" if direction == "debit" else "DEBIT",
            amount=amount,
            day=created_at.astimezone(IST).date(),
            detail=kind,
        )
        for direction, amount, created_at, kind, reference in rows.all()
    ]


async def _transfer_references(session: AsyncSession, first: date, last: date) -> dict[str, str]:
    """Transfer ID -> client_reference for the window's successful payments, asking the bank for any transfer ID
    not yet known (for example a payment settled before transfer IDs were kept)."""
    start, _ = _day_bounds(first - timedelta(days=1))
    _, end = _day_bounds(last)
    payments = (
        await session.scalars(
            select(Payment).where(
                Payment.status == "SUCCESS",
                Payment.bank_transaction_id.is_not(None),
                Payment.completed_at >= start,
                Payment.completed_at < end,
            )
        )
    ).all()
    missing = [p for p in payments if p.bank_transfer_id is None]
    if missing:
        bank = await get_payment_provider(session)
        for p in missing:
            p.bank_transfer_id = await transfer_of(bank, p.bank_transaction_id)
    return {p.bank_transfer_id: p.client_reference for p in payments if p.bank_transfer_id}


def _key(line: _Line) -> tuple[str | None, str, int]:
    return (line.reference, line.direction, line.amount)


async def reconcile(session: AsyncSession, business_date: date, ran_by: str) -> ReconciliationRun:
    plugin = await bank_plugin(session)
    escrow_upi = str(plugin.config.get("escrow_upi_id") or "")
    account = await ledger.account(session, ledger.escrow_code(plugin.key), "asset", provider=plugin.key)
    issues: list[dict[str, object]] = []
    bank = await get_issuer(session)

    sums = await totals(session)
    if sums.escrow != sums.wallets + sums.in_transit:
        issues.append(
            {
                "type": "ledger_out_of_balance",
                "escrow": sums.escrow,
                "wallets": sums.wallets,
                "in_transit": sums.in_transit,
            }
        )

    bank_balance: int | None = None
    try:
        bank_balance = (await bank.get_balance(escrow_upi)).amount_paise
    except BankError as exc:
        issues.append({"type": "bank_balance_unavailable", "detail": exc.code})
    if bank_balance is not None and bank_balance != account.balance:
        issues.append(
            {
                "type": "balance_mismatch",
                "bank": bank_balance,
                "ledger": account.balance,
                "difference": bank_balance - account.balance,
            }
        )

    first, last = business_date - timedelta(days=1), business_date + timedelta(days=1)
    matched = 0
    try:
        statement = await _statement(bank, escrow_upi, first, last)
    except BankError as exc:
        issues.append({"type": "statement_unavailable", "detail": exc.code})
        statement = []
    else:
        by_transfer = await _transfer_references(session, first, last)
        bank_lines = [
            _Line(
                reference=t.client_reference or by_transfer.get(t.transfer_id or ""),
                direction="CREDIT" if t.direction == Direction.CREDIT else "DEBIT",
                amount=t.amount_paise,
                day=t.tran_date,
                detail=t.narration or t.tran_type,
            )
            for t in statement
        ]
        ledger_lines = await _ledger_lines(session, account, first, last)
        unmatched_ledger = [_key(line) for line in ledger_lines]
        for line in bank_lines:
            if _key(line) in unmatched_ledger:
                unmatched_ledger.remove(_key(line))
                matched += line.day == business_date
            elif line.day == business_date:
                issues.append(
                    {
                        "type": "bank_only",  # money moved at the bank with no ledger entry
                        "client_reference": line.reference,
                        "direction": line.direction,
                        "amount": line.amount,
                        "detail": line.detail,
                    }
                )
        for line in ledger_lines:
            if _key(line) in unmatched_ledger and line.day == business_date:
                unmatched_ledger.remove(_key(line))
                issues.append(
                    {
                        "type": "ledger_only",  # the ledger recorded money the bank statement doesn't show
                        "client_reference": line.reference,
                        "direction": line.direction,
                        "amount": line.amount,
                        "detail": line.detail,
                    }
                )

    run = ReconciliationRun(
        provider=plugin.key,
        business_date=business_date,
        ran_by=ran_by,
        bank_balance=bank_balance,
        ledger_balance=account.balance,
        matched=matched,
        clean=not issues,
        issues=issues,
    )
    session.add(run)
    await session.commit()
    if issues:
        # Stands in for opening an operations case until case management exists (M8, M14).
        log.warning("RECONCILIATION MISMATCH", extra={"date": str(business_date), "issues": len(issues), "run": run.id})
    return run


async def latest_runs(session: AsyncSession, limit: int = 30) -> list[ReconciliationRun]:
    return list(
        (await session.scalars(select(ReconciliationRun).order_by(ReconciliationRun.id.desc()).limit(limit))).all()
    )


async def ran_today_for(session: AsyncSession, business_date: date, ran_by: str) -> bool:
    found = await session.scalar(
        select(ReconciliationRun.id).where(
            ReconciliationRun.business_date == business_date, ReconciliationRun.ran_by == ran_by
        )
    )
    return found is not None


@dataclass(frozen=True)
class LedgerTotals:
    escrow: int
    in_transit: int
    wallets: int  # all customers' wallet balances together


async def totals(session: AsyncSession) -> LedgerTotals:
    accounts = (await session.scalars(select(LedgerAccount))).all()
    return LedgerTotals(
        escrow=sum(a.balance for a in accounts if a.code.startswith("escrow:")),
        in_transit=sum(a.balance for a in accounts if a.code.startswith("in_transit:")),
        wallets=sum(a.balance for a in accounts if a.customer_id is not None),
    )
