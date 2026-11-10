"""Consents per purpose (plan M9). Notice texts and versions come from config/notices.yaml."""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import load_yaml_config
from paymentsapp.core.errors import AppError
from paymentsapp.modules.audit.service import Actor, record
from paymentsapp.modules.consent.models import ConsentRecord

TERMS = "terms_and_privacy"


@dataclass(frozen=True)
class Notice:
    purpose: str
    required: bool
    version: str
    title: str
    summary: str


@lru_cache
def notices() -> dict[str, Notice]:
    data: dict[str, Any] = load_yaml_config("notices")["purposes"]
    return {
        purpose: Notice(purpose, bool(n["required"]), str(n["version"]), n["title"], " ".join(n["summary"].split()))
        for purpose, n in data.items()
    }


def _notice(purpose: str) -> Notice:
    notice = notices().get(purpose)
    if notice is None:
        raise AppError("NOT_FOUND", "no such consent purpose", status_code=404)
    return notice


async def _active(session: AsyncSession, customer_id: uuid.UUID, purpose: str) -> ConsentRecord | None:
    return await session.scalar(
        select(ConsentRecord).where(
            ConsentRecord.customer_id == customer_id,
            ConsentRecord.purpose == purpose,
            ConsentRecord.withdrawn_at.is_(None),
        )
    )


async def grant(
    session: AsyncSession, customer_id: uuid.UUID, actor: Actor, purpose: str, version: str
) -> ConsentRecord:
    """Record consent to the current notice. An older acceptance is closed and replaced by the new one."""
    notice = _notice(purpose)
    if version != notice.version:
        raise AppError("NOTICE_CHANGED", f"accept the current notice (version {notice.version})", status_code=409)
    now = datetime.now(UTC)
    existing = await _active(session, customer_id, purpose)
    if existing is not None:
        if existing.notice_version == version:
            return existing
        existing.withdrawn_at = now
        await session.flush()
    consent = ConsentRecord(customer_id=customer_id, purpose=purpose, notice_version=version, granted_at=now)
    session.add(consent)
    await session.flush()
    await record(
        session, actor, "consent.granted", "customer", str(customer_id), after={"purpose": purpose, "version": version}
    )
    return consent


async def withdraw(session: AsyncSession, customer_id: uuid.UUID, actor: Actor, purpose: str) -> None:
    notice = _notice(purpose)
    if notice.required:
        raise AppError("CONSENT_REQUIRED", "this consent is needed to run your account; close the account instead", 409)
    existing = await _active(session, customer_id, purpose)
    if existing is None:
        return
    existing.withdrawn_at = datetime.now(UTC)
    await session.flush()
    # Purging data held only for this purpose (plan M9) hooks in here once AI insights store data (Phase 5).
    await record(session, actor, "consent.withdrawn", "customer", str(customer_id), after={"purpose": purpose})


@dataclass(frozen=True)
class ConsentStatus:
    notice: Notice
    granted: bool
    accepted_version: str | None
    granted_at: datetime | None

    @property
    def needs_acceptance(self) -> bool:
        return self.notice.required and self.accepted_version != self.notice.version


async def statuses(session: AsyncSession, customer_id: uuid.UUID) -> list[ConsentStatus]:
    active = {
        c.purpose: c
        for c in (
            await session.scalars(
                select(ConsentRecord).where(
                    ConsentRecord.customer_id == customer_id, ConsentRecord.withdrawn_at.is_(None)
                )
            )
        ).all()
    }
    return [
        ConsentStatus(
            notice=n,
            granted=p in active,
            accepted_version=active[p].notice_version if p in active else None,
            granted_at=active[p].granted_at if p in active else None,
        )
        for p, n in notices().items()
    ]
