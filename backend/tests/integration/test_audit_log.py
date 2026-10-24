"""Audit log (plan M15): entries chain correctly, the table refuses changes, and tampering is detected."""

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.logging import request_id_var
from paymentsapp.modules.audit.hash_chain import GENESIS_HASH
from paymentsapp.modules.audit.models import AuditEntry
from paymentsapp.modules.audit.service import Actor, record, verify_chain

ADMIN = Actor("admin", "adm_1", ip="10.0.0.5")


async def add_three(session: AsyncSession) -> list[AuditEntry]:
    entries = [
        await record(session, ADMIN, "plugin.change_requested", "plugin", "mockbank", after={"mode": "mock"}),
        await record(session, Actor("admin", "adm_2"), "plugin.change_approved", "plugin", "mockbank"),
        await record(
            session,
            ADMIN,
            "control.changed",
            "control",
            "otp_requests_per_hour",
            before={"value": 50},
            after={"value": 5},
        ),
    ]
    await session.commit()
    return entries


async def test_entries_are_chained_and_verify(session: AsyncSession) -> None:
    first, second, third = await add_three(session)

    assert first.prev_hash == GENESIS_HASH
    assert second.prev_hash == first.hash
    assert third.prev_hash == second.hash
    result = await verify_chain(session)
    assert result.ok and result.entries_checked == 3


async def test_request_id_is_recorded(session: AsyncSession) -> None:
    token = request_id_var.set("req-abc12345")
    try:
        entry = await record(session, ADMIN, "admin.signed_in", "admin_user", "adm_1")
    finally:
        request_id_var.reset(token)
    assert entry.request_id == "req-abc12345"


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_log SET action = 'something.else'",
        "DELETE FROM audit_log",
        "TRUNCATE audit_log",
    ],
    ids=["update", "delete", "truncate"],
)
async def test_table_rejects_changes(session: AsyncSession, statement: str) -> None:
    await add_three(session)
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(text(statement))
    assert await session.scalar(select(func.count()).select_from(AuditEntry)) == 3


async def test_changed_entry_is_detected(session: AsyncSession) -> None:
    _, second, _ = await add_three(session)

    # Simulates someone with owner rights bypassing the trigger. Rolled back with the test's transaction.
    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    await session.execute(
        text('UPDATE audit_log SET after = \'{"mode": "production"}\' WHERE id = :id'), {"id": second.id}
    )
    await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    session.expire_all()

    result = await verify_chain(session)
    assert not result.ok
    assert result.first_bad_id == second.id
    assert result.reason is not None and "changed" in result.reason


async def test_removed_entry_is_detected(session: AsyncSession) -> None:
    _, second, third = await add_three(session)

    await session.execute(text("ALTER TABLE audit_log DISABLE TRIGGER audit_log_append_only"))
    await session.execute(text("DELETE FROM audit_log WHERE id = :id"), {"id": second.id})
    await session.execute(text("ALTER TABLE audit_log ENABLE TRIGGER audit_log_append_only"))
    session.expire_all()

    result = await verify_chain(session)
    assert not result.ok
    assert result.first_bad_id == third.id
    assert result.reason is not None and "removed" in result.reason


async def test_rolled_back_change_leaves_no_entry(session: AsyncSession) -> None:
    async with session.begin_nested() as nested:
        await record(session, ADMIN, "plugin.change_requested", "plugin", "mockbank")
        await nested.rollback()
    assert await session.scalar(select(func.count()).select_from(AuditEntry)) == 0
