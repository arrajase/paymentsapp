"""Handling bank events, whether they arrive by webhook or by polling the bank's /events feed (plan 9.4, 9.5).

Each event is claimed by its ID before it is handled, in the same transaction, so a repeat delivery (or the same
event arriving both ways) is handled once. If handling fails, the transaction rolls back, the claim goes with it,
and the event is handled afresh next time.

The poller catches up on events the webhook missed, for example while the hosted bank couldn't reach a backend
running on a laptop. It runs in the worker (paymentsapp.worker), one round at a time.
"""

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.integrations.bank_gateway import BankError, BankEvent
from paymentsapp.integrations.registry import ProviderUnavailable, get_bank_event_source
from paymentsapp.modules.bankevents.models import BankEventCursor, BankEventReceipt
from paymentsapp.modules.payments import service as payments
from paymentsapp.modules.upi import service as upi

log = logging.getLogger(__name__)

Source = Literal["webhook", "poll"]
Outcome = Literal["processed", "ignored", "duplicate"]

HANDLED_TYPES = {"consent.updated", "collect_request.updated"}
# transaction.posted (movements on the app's own accounts) feeds reconciliation, which reads statements instead.

PAGE_SIZE = 100
MAX_PAGES_PER_ROUND = 10
# Arbitrary constant naming the advisory lock that lets only one poller read a bank's feed at a time.
_POLLER_LOCK = 7_350_002


def _now() -> datetime:
    return datetime.now(UTC)


async def _claim(session: AsyncSession, provider: str, event: BankEvent, outcome: str, source: Source) -> bool:
    inserted = await session.scalar(
        insert(BankEventReceipt)
        .values(
            event_id=event.event_id,
            provider=provider,
            type=event.type[:64],
            occurred_at=event.created_at,
            outcome=outcome,
            source=source,
        )
        .on_conflict_do_nothing(index_elements=[BankEventReceipt.event_id])
        .returning(BankEventReceipt.event_id)
    )
    return inserted is not None


async def handle(session: AsyncSession, provider: str, event: BankEvent, source: Source) -> Outcome:
    """Claim and handle one event in the caller's transaction; the caller commits. BankError propagates, so the
    caller can roll back and let the event come again."""
    handled = event.type in HANDLED_TYPES
    if not await _claim(session, provider, event, "processed" if handled else "ignored", source):
        return "duplicate"
    if event.type == "consent.updated":
        consent_id = event.data.get("consent_id")
        if isinstance(consent_id, str):
            await upi.on_consent_updated(session, consent_id)
    elif event.type == "collect_request.updated":
        collect_id = event.data.get("collect_id")
        if isinstance(collect_id, str):
            await payments.on_collect_updated(session, collect_id)
    return "processed" if handled else "ignored"


@dataclass
class PollResult:
    ran: bool  # False when another poller holds the lock
    counts: dict[str, int] = field(default_factory=lambda: {"processed": 0, "ignored": 0, "duplicate": 0})
    error: str | None = None


async def poll_once(session: AsyncSession, provider: str = "mockbank") -> PollResult:
    """Read new events from the bank's feed and handle them, oldest first. The cursor moves past an event only
    when that event was handled, in the same transaction, so a crash never skips one."""
    locked = await session.scalar(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": _POLLER_LOCK})
    if not locked:
        await session.rollback()
        return PollResult(ran=False)

    cursor = await session.get(BankEventCursor, provider, with_for_update=True)
    if cursor is None:
        cursor = BankEventCursor(provider=provider, events_handled=0)
        session.add(cursor)
    result = PollResult(ran=True)
    cursor.last_polled_at = _now()

    try:
        source = await get_bank_event_source(session)
        for _ in range(MAX_PAGES_PER_ROUND):
            events = await source.poll_events(after=cursor.last_event_id, limit=PAGE_SIZE)
            for event in events:
                # A savepoint per event: one that fails rolls back alone, and the round stops before it.
                async with session.begin_nested():
                    outcome = await handle(session, provider, event, "poll")
                cursor.last_event_id = event.event_id
                cursor.events_handled += 1
                result.counts[outcome] += 1
            if len(events) < PAGE_SIZE:
                break
    except BankError as exc:
        result.error = f"{exc.code}: {exc.message}"[:500]
        log.warning("bank event poll stopped early", extra={"provider": provider, "code": exc.code})
    except ProviderUnavailable:
        result.error = "the plugin is disabled or missing its URL or API key"

    cursor.last_error = result.error
    if result.error is None:
        cursor.last_success_at = cursor.last_polled_at
    await session.commit()
    return result
