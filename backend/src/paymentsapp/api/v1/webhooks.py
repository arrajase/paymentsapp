"""Bank webhooks (plan 9.4). Public, so nothing is trusted until the signature on the raw bytes checks out.

Each event is claimed once (duplicates are acknowledged and skipped) and handled in the same transaction. If
handling fails, nothing is kept and the answer is 503, so the bank delivers the event again later.
"""

import logging

from fastapi import APIRouter, Request

from paymentsapp.api.deps import DbSession
from paymentsapp.core.errors import AppError
from paymentsapp.integrations.bank_gateway import BankError, WebhookVerificationError
from paymentsapp.integrations.bankevents import inbox
from paymentsapp.integrations.registry import MOCKBANK, get_bank_event_source
from paymentsapp.modules.upi import service as upi

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

    handled = event.type == "consent.updated"
    if not await inbox.claim(session, MOCKBANK, event, "processed" if handled else "ignored"):
        await session.commit()
        return {"status": "duplicate"}
    try:
        consent_id = event.data.get("consent_id")
        if handled and isinstance(consent_id, str):
            await upi.on_consent_updated(session, consent_id)
        # transaction.posted and collect_request.updated are handled once the wallet exists (M4, M5).
    except BankError:
        await session.rollback()
        raise AppError("SERVICE_UNAVAILABLE", "try again later", status_code=503) from None
    await session.commit()
    return {"status": "ok"}
