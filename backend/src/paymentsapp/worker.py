"""Background worker. Run from backend/, next to the API:

    uv run python -m paymentsapp.worker

Every BANK_EVENT_POLL_SECONDS it:
- reads the bank's /events feed and handles any event the webhook missed (plan 9.5);
- settles payments still waiting on the bank: collect requests awaiting approval, and UNKNOWN payments whose
  answer was lost, retried with their original idempotency key (plan M5, 9.4);
- once a day, reconciles the previous day's escrow movements with the bank (plan M4).
Running two workers is safe: a database lock lets only one poll at a time, and each payment is settled under a row
lock. Stop it with Ctrl+C.
"""

import asyncio
import logging
from datetime import datetime, timedelta

from paymentsapp.core.config import get_settings
from paymentsapp.core.logging import configure_logging
from paymentsapp.db.session import get_session
from paymentsapp.integrations.registry import ProviderUnavailable, close_clients
from paymentsapp.modules.bankevents.service import poll_once
from paymentsapp.modules.compliance.limits import IST
from paymentsapp.modules.payments.service import sweep
from paymentsapp.modules.wallet.reconciliation import ran_today_for, reconcile

log = logging.getLogger("paymentsapp.worker")


async def poll_round() -> None:
    async for session in get_session():
        result = await poll_once(session)
        if not result.ran:
            log.info("another worker is polling; skipped this round")
        elif result.error:
            log.warning("bank event poll failed", extra={"error": result.error, **result.counts})
        elif any(result.counts.values()):
            log.info("bank events handled", extra=result.counts)


async def settle_round() -> None:
    async for session in get_session():
        settled = await sweep(session)
        if settled:
            log.info("payments checked with the bank", extra={"count": settled})


async def reconcile_round() -> None:
    """Yesterday (IST), once: the bank's statement for a day is complete only after it ends."""
    yesterday = datetime.now(IST).date() - timedelta(days=1)
    async for session in get_session():
        if await ran_today_for(session, yesterday, "worker"):
            return
        try:
            result = await reconcile(session, yesterday, "worker")
        except ProviderUnavailable:
            return
        log.info("reconciled", extra={"date": str(yesterday), "clean": result.clean, "matched": result.matched})


async def run() -> None:
    interval = get_settings().bank_event_poll_seconds
    log.info("worker started", extra={"poll_seconds": interval})
    try:
        while True:
            # One bad round (database blip, unexpected reply) must not stop the worker.
            try:
                await poll_round()
            except Exception:
                log.exception("poll round failed")
            try:
                await settle_round()
            except Exception:
                log.exception("settle round failed")
            try:
                await reconcile_round()
            except Exception:
                log.exception("reconciliation round failed")
            await asyncio.sleep(interval)
    finally:
        await close_clients()


def main() -> None:
    configure_logging(get_settings().log_level)
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        log.info("worker stopped")


if __name__ == "__main__":
    main()
