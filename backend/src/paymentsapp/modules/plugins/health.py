"""Plugin health for the admin website (plan 3.1, M17).

The overview is cheap: stored settings, this backend's circuit breaker, the last on-demand check, and how bank
events have been arriving. A check is run only when an admin asks for one, because waking the hosted mock bank can
take most of a minute.
"""

import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.errors import AppError
from paymentsapp.integrations.bank_gateway import BankError
from paymentsapp.integrations.registry import MOCKBANK, ProviderUnavailable, bank_circuit_state, get_bank_client
from paymentsapp.modules.admin.service import AdminContext
from paymentsapp.modules.bankevents.models import BankEventCursor, BankEventReceipt
from paymentsapp.modules.plugins.models import PluginHealthCheck, PluginSetting

# Plugins that call a partner over the network. The others run inside the backend, so there's nothing to reach.
REMOTE = {MOCKBANK}
WAKE_SECONDS = 60.0


def _now() -> datetime:
    return datetime.now(UTC)


@dataclass(frozen=True)
class EventFeed:
    last_webhook_at: datetime | None
    last_poll_at: datetime | None
    last_poll_success_at: datetime | None
    last_poll_error: str | None
    received_24h: int


@dataclass(frozen=True)
class PluginHealth:
    setting: PluginSetting
    remote: bool
    circuit: str | None
    last_check: PluginHealthCheck | None
    events: EventFeed | None


async def _event_feed(session: AsyncSession, provider: str) -> EventFeed:
    last_webhook = await session.scalar(
        select(func.max(BankEventReceipt.received_at)).where(
            BankEventReceipt.provider == provider, BankEventReceipt.source == "webhook"
        )
    )
    received = await session.scalar(
        select(func.count()).where(
            BankEventReceipt.provider == provider, BankEventReceipt.received_at > _now() - timedelta(days=1)
        )
    )
    cursor = await session.get(BankEventCursor, provider)
    return EventFeed(
        last_webhook_at=last_webhook,
        last_poll_at=cursor.last_polled_at if cursor else None,
        last_poll_success_at=cursor.last_success_at if cursor else None,
        last_poll_error=cursor.last_error if cursor else None,
        received_24h=received or 0,
    )


async def overview(session: AsyncSession) -> list[PluginHealth]:
    settings = (await session.scalars(select(PluginSetting).order_by(PluginSetting.key))).all()
    checks = {c.plugin_key: c for c in (await session.scalars(select(PluginHealthCheck))).all()}
    return [
        PluginHealth(
            setting=s,
            remote=s.key in REMOTE,
            circuit=bank_circuit_state() if s.key == MOCKBANK else None,
            last_check=checks.get(s.key),
            events=await _event_feed(session, s.key) if s.key == MOCKBANK else None,
        )
        for s in settings
    ]


async def _probe_bank(session: AsyncSession) -> tuple[bool, int | None, str]:
    try:
        client = await get_bank_client(session)
    except ProviderUnavailable:
        return False, None, "disabled, or missing its URL or API key"
    started = time.perf_counter()
    try:
        await client.warm_up(max_wait_seconds=WAKE_SECONDS)
    except BankError:
        return False, None, f"not reachable within {WAKE_SECONDS:.0f} seconds"
    latency = round((time.perf_counter() - started) * 1000)
    try:
        await client.poll_events(limit=1)  # any call that needs the API key
    except BankError as exc:
        if exc.code == "UNAUTHORIZED":
            return False, latency, "reachable, but the API key was refused"
        return False, latency, f"reachable, but an API call failed ({exc.code})"
    return True, latency, "reachable, and the API key works"


async def check(session: AsyncSession, admin: AdminContext, plugin_key: str) -> PluginHealthCheck:
    setting = await session.get(PluginSetting, plugin_key)
    if setting is None:
        raise AppError("NOT_FOUND", "no such plugin", status_code=404)
    if plugin_key not in REMOTE:
        raise AppError("NOTHING_TO_CHECK", "this plugin runs inside the backend; there is nothing to reach", 409)
    ok, latency, detail = await _probe_bank(session)
    result = await session.get(PluginHealthCheck, plugin_key)
    if result is None:
        result = PluginHealthCheck(plugin_key=plugin_key)
        session.add(result)
    result.checked_at = _now()
    result.ok = ok
    result.latency_ms = latency
    result.detail = detail
    result.checked_by = str(admin.user.id)
    await session.commit()
    return result
