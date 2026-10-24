"""Turn a verified mock bank webhook into a BankEvent. Verification always happens on the raw bytes, first."""

import json
from collections.abc import Mapping

from pydantic import ValidationError

from paymentsapp.integrations.bank_gateway import BankEvent, WebhookVerificationError
from paymentsapp.integrations.mockbank.schemas import EventOut
from paymentsapp.integrations.mockbank.signing import EVENT_ID_HEADER, SIGNATURE_HEADER, verify_signature


def _header(headers: Mapping[str, str], name: str) -> str | None:
    wanted = name.lower()
    return next((v for k, v in headers.items() if k.lower() == wanted), None)


def to_event(e: EventOut) -> BankEvent:
    return BankEvent(event_id=e.event_id, type=e.type, created_at=e.created_at, data=e.data)


def parse_webhook(secret: str, raw_body: bytes, headers: Mapping[str, str]) -> BankEvent:
    verify_signature(secret, _header(headers, SIGNATURE_HEADER), raw_body)
    try:
        event = EventOut.model_validate(json.loads(raw_body))
    except (ValueError, ValidationError):
        raise WebhookVerificationError("body is not a valid event") from None
    header_id = _header(headers, EVENT_ID_HEADER)
    if header_id is not None and header_id != event.event_id:
        raise WebhookVerificationError("event id header does not match the body")
    return to_event(event)
