"""PII never reaches the logs (plan M15, 7.3)."""

import json
import logging

import pytest

from paymentsapp.core.logging import JsonFormatter, redact


@pytest.mark.parametrize(
    ("raw", "must_not_contain"),
    [
        ("customer jane.doe+1@example.co.in signed in", "jane.doe"),
        ("linking jane@okmockbank", "jane@okmockbank"),
        ("otp sent to 9876543210", "9876543210"),
        ("otp sent to +91 9876543210", "9876543210"),
        ("account 123456789012 credited", "123456789012"),
        ("aadhaar 1234 5678 9012", "1234 5678 9012"),
        ("pan ABCDE1234F verified", "ABCDE1234F"),
        ("x-api-key: mbk_ab12_secretpart", "secretpart"),
        ("Authorization: Bearer abc.def.ghi", "abc.def.ghi"),
        ('{"password": "hunter2"}', "hunter2"),
        ("pin=4321", "4321"),
        ("token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.sig_part", "eyJzdWIiOiIxIn0"),
    ],
)
def test_redact_removes_pii(raw: str, must_not_contain: str) -> None:
    assert must_not_contain not in redact(raw)


def test_redact_keeps_ordinary_text_and_small_numbers() -> None:
    text = "payment pay_123 moved 12000 paise in 3 attempts"
    assert redact(text) == text


def test_formatter_redacts_message_args_and_extra_fields() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "linked %s", ("jane@okmockbank",), None)
    record.payload = {"phone": "9876543210", "nested": ["mail me at a@b.com"]}

    line = JsonFormatter().format(record)

    assert "jane@okmockbank" not in line
    assert "9876543210" not in line
    assert "a@b.com" not in line
    entry = json.loads(line)
    assert entry["level"] == "INFO"
    assert entry["payload"]["phone"] == "<phone>"
