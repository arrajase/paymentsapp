"""Write and verify audit entries.

record() joins the caller's transaction: the audit entry commits or rolls back together with the change it
describes, so there is never a change without its entry, or an entry for a change that didn't happen.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.logging import request_id_var
from paymentsapp.modules.audit.hash_chain import GENESIS_HASH, HASHED_FIELDS, entry_hash
from paymentsapp.modules.audit.models import AuditEntry

ActorType = Literal["customer", "admin", "system"]

# Arbitrary constant naming the advisory lock that serialises audit writers. Held until the transaction ends, so
# entries are chained in commit order even when many requests write at once.
_AUDIT_CHAIN_LOCK = 7_350_001


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None = None
    ip: str | None = None
    device_id: str | None = None


SYSTEM = Actor("system", "system")


async def record(
    session: AsyncSession,
    actor: Actor,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    *,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
) -> AuditEntry:
    """Append an entry in the caller's transaction. Pass masked values for restricted data (plan 7.3)."""
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _AUDIT_CHAIN_LOCK})
    prev_hash = await session.scalar(select(AuditEntry.hash).order_by(AuditEntry.id.desc()).limit(1))

    fields: dict[str, Any] = {
        # Microseconds are dropped: the hash must match what Postgres stores and returns.
        "occurred_at": datetime.now(UTC).replace(microsecond=0),
        "actor_type": actor.type,
        "actor_id": actor.id,
        "action": action,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "request_id": request_id_var.get(),
        "ip": actor.ip,
        "device_id": actor.device_id,
        "before": before,
        "after": after,
    }
    previous = prev_hash or GENESIS_HASH
    entry = AuditEntry(**fields, prev_hash=previous, hash=entry_hash(previous, fields))
    session.add(entry)
    await session.flush()
    return entry


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    entries_checked: int
    first_bad_id: int | None = None
    reason: str | None = None


async def verify_chain(session: AsyncSession, batch_size: int = 1000) -> ChainCheck:
    """Recompute every hash in id order. Reports the first entry that doesn't match."""
    expected_prev = GENESIS_HASH
    checked = 0
    last_id = 0
    while True:
        rows = (
            await session.scalars(
                select(AuditEntry).where(AuditEntry.id > last_id).order_by(AuditEntry.id).limit(batch_size)
            )
        ).all()
        if not rows:
            return ChainCheck(ok=True, entries_checked=checked)
        for row in rows:
            if row.prev_hash != expected_prev:
                return ChainCheck(False, checked, row.id, "previous hash does not match: an entry was removed")
            fields = {name: getattr(row, name) for name in HASHED_FIELDS}
            if entry_hash(row.prev_hash, fields) != row.hash:
                return ChainCheck(False, checked, row.id, "hash does not match: the entry was changed")
            expected_prev = row.hash
            checked += 1
            last_id = row.id
