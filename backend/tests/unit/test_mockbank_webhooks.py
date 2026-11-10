"""Webhook verification: only fresh, correctly signed, well-formed events get through."""

import json
import time
from collections.abc import Callable

import pytest

from paymentsapp.integrations.bank_gateway import WebhookVerificationError
from paymentsapp.integrations.mockbank.signing import compute_signature, verify_signature
from paymentsapp.integrations.mockbank.webhooks import parse_webhook

SECRET = "whsec_test"
BODY = json.dumps(
    {"event_id": "evt_1", "type": "transaction.posted", "created_at": "2026-10-03T09:15:02Z", "data": {"x": 1}}
).encode()


def signed_headers(body: bytes = BODY, *, timestamp: int | None = None, secret: str = SECRET) -> dict[str, str]:
    t = int(time.time()) if timestamp is None else timestamp
    return {
        "x-mockbank-signature": f"t={t},v1={compute_signature(secret, t, body)}",
        "x-mockbank-event-id": "evt_1",
    }


def test_valid_webhook_is_parsed() -> None:
    event = parse_webhook(SECRET, BODY, signed_headers())
    assert event.event_id == "evt_1"
    assert event.type == "transaction.posted"
    assert event.data == {"x": 1}


def test_header_names_are_case_insensitive() -> None:
    headers = {k.title(): v for k, v in signed_headers().items()}
    assert parse_webhook(SECRET, BODY, headers).event_id == "evt_1"


# Headers are built when the test runs, not at collection: a long test run would otherwise age the timestamps.
@pytest.mark.parametrize(
    "make_headers",
    [
        lambda: {},
        lambda: {"X-MockBank-Signature": "garbage"},
        lambda: {"X-MockBank-Signature": "t=notanumber,v1=abc"},
        lambda: signed_headers(secret="wrong-secret"),
        lambda: signed_headers(timestamp=int(time.time()) - 900),
        lambda: signed_headers(timestamp=int(time.time()) + 900),
    ],
    ids=["missing", "malformed", "bad-timestamp", "wrong-secret", "stale", "future"],
)
def test_bad_signatures_are_rejected(make_headers: Callable[[], dict[str, str]]) -> None:
    headers = make_headers()
    with pytest.raises(WebhookVerificationError):
        parse_webhook(SECRET, BODY, headers)


def test_tampered_body_is_rejected() -> None:
    headers = signed_headers()
    with pytest.raises(WebhookVerificationError):
        parse_webhook(SECRET, BODY.replace(b'"x": 1', b'"x": 2'), headers)


def test_event_id_header_must_match_body() -> None:
    headers = signed_headers()
    headers["x-mockbank-event-id"] = "evt_other"
    with pytest.raises(WebhookVerificationError):
        parse_webhook(SECRET, BODY, headers)


def test_signed_but_invalid_body_is_rejected() -> None:
    body = b'{"not": "an event"}'
    with pytest.raises(WebhookVerificationError):
        parse_webhook(SECRET, body, signed_headers(body))


def test_any_matching_v1_is_accepted_during_secret_rotation() -> None:
    t = int(time.time())
    header = f"t={t},v1={compute_signature('old', t, BODY)},v1={compute_signature(SECRET, t, BODY)}"
    verify_signature(SECRET, header, BODY)
