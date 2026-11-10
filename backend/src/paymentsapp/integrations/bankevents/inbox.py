"""Claiming a bank event before handling it, so a repeated delivery is acknowledged without being handled again."""

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.integrations.bank_gateway import BankEvent
from paymentsapp.integrations.bankevents.models import BankEventReceipt


async def claim(session: AsyncSession, provider: str, event: BankEvent, outcome: str) -> bool:
    """Record the event in the caller's transaction. False if it was already received.

    The caller handles the event in the same transaction, so if handling fails and rolls back, the claim goes too
    and the bank's next delivery is handled afresh.
    """
    inserted = await session.scalar(
        insert(BankEventReceipt)
        .values(
            event_id=event.event_id,
            provider=provider,
            type=event.type[:64],
            occurred_at=event.created_at,
            outcome=outcome,
        )
        .on_conflict_do_nothing(index_elements=[BankEventReceipt.event_id])
        .returning(BankEventReceipt.event_id)
    )
    return inserted is not None
