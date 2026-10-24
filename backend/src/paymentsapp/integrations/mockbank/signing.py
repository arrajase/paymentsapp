"""Webhook signature check: X-MockBank-Signature: t=<unix seconds>,v1=<hex HMAC-SHA256 of "t.raw_body">."""

import hashlib
import hmac
import time

from paymentsapp.integrations.bank_gateway import WebhookVerificationError

SIGNATURE_HEADER = "X-MockBank-Signature"
EVENT_ID_HEADER = "X-MockBank-Event-Id"
DEFAULT_TOLERANCE_SECONDS = 300  # plan 9.4: timestamps within 5 minutes


def compute_signature(secret: str, timestamp: int, raw_body: bytes) -> str:
    return hmac.new(secret.encode(), f"{timestamp}.".encode() + raw_body, hashlib.sha256).hexdigest()


def verify_signature(
    secret: str,
    header: str | None,
    raw_body: bytes,
    *,
    tolerance_seconds: int = DEFAULT_TOLERANCE_SECONDS,
    now: float | None = None,
) -> None:
    """Raise WebhookVerificationError unless the header is a valid, fresh signature of exactly these bytes."""
    if not header:
        raise WebhookVerificationError("missing signature header")

    parts: dict[str, list[str]] = {}
    for item in header.split(","):
        key, sep, value = item.strip().partition("=")
        if sep:
            parts.setdefault(key, []).append(value)

    try:
        timestamp = int(parts["t"][0])
    except (KeyError, ValueError):
        raise WebhookVerificationError("malformed signature header") from None

    current = time.time() if now is None else now
    if abs(current - timestamp) > tolerance_seconds:
        raise WebhookVerificationError("stale or future timestamp")

    expected = compute_signature(secret, timestamp, raw_body)
    if not any(hmac.compare_digest(expected, candidate) for candidate in parts.get("v1", [])):
        raise WebhookVerificationError("signature mismatch")
