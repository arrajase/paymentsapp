"""Bank webhooks (plan 9.4). Public, so nothing is trusted until the signature on the raw bytes checks out.

Events are handled by modules/bankevents, the same way the poller handles them. A duplicate is acknowledged and
skipped. If handling fails, nothing is kept and the answer is 503, so the bank delivers the event again later.
"""

import logging

from fastapi import APIRouter, Request

from paymentsapp.api.deps import DbSession
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.bank_gateway import BankError, WebhookVerificationError
from paymentsapp.integrations.registry import MOCKBANK, get_bank_event_source
from paymentsapp.modules.bankevents import service as bank_events

log = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/webhooks", tags=["webhooks"])

MAX_BODY_BYTES = 64 * 1024


def _too_large() -> AppError:
    return AppError("PAYLOAD_TOO_LARGE", "request body too large", status_code=413)


@router.post("/mockbank", include_in_schema=False)
async def mockbank(request: Request, session: DbSession) -> dict[str, str]:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise _too_large()
    raw = await request.body()
    if len(raw) > MAX_BODY_BYTES:
        raise _too_large()

    source = await get_bank_event_source(session)
    try:
        event = source.verify_webhook(raw, request.headers)
    except WebhookVerificationError as exc:
        log.warning("rejected a mock bank webhook", extra={"reason": str(exc)})
        raise AppError("INVALID_SIGNATURE", "signature check failed", status_code=401) from None
    except RuntimeError:
        log.error("mock bank webhook received, but the plugin has no webhook secret")
        raise AppError("SERVICE_UNAVAILABLE", "webhooks are not configured", status_code=503) from None

    try:
        outcome = await bank_events.handle(session, MOCKBANK, event, "webhook")
    except BankError:
        await session.rollback()
        raise AppError("SERVICE_UNAVAILABLE", "try again later", status_code=503) from None
    await session.commit()
    return {"status": "duplicate" if outcome == "duplicate" else "ok"}
