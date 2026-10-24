"""Structured JSON logging with PII redaction (plan M15, 7.3).

Every log line passes through `redact`, so phone numbers, emails, UPI IDs, account numbers, PAN, Aadhaar
numbers and secrets never reach log storage, even when a developer logs a whole request by mistake.
Over-redaction is accepted: a masked timestamp is a smaller problem than a leaked account number.
"""

import json
import logging
import re
from contextvars import ContextVar
from datetime import UTC, datetime
from typing import Any

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)

# Order matters: secrets and emails are matched before the shorter patterns that would split them.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+"), "Bearer <redacted>"),
    (re.compile(r"\bmbk_[A-Za-z0-9_]+"), "<api-key>"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"), "<jwt>"),
    (
        re.compile(
            r"(?i)\b(api[_-]?key|x-api-key|token|secret|password|passwd|pin|otp|authorization)"
            r"(\"?\s*[:=]\s*\"?)[^\s\"',}]+"
        ),
        r"\1\2<redacted>",
    ),
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "<email>"),
    (re.compile(r"\b[\w.-]+@[A-Za-z][A-Za-z0-9]*\b"), "<upi-id>"),
    (re.compile(r"\b[A-Z]{5}[0-9]{4}[A-Z]\b"), "<pan>"),
    (re.compile(r"\b\d{4}\s\d{4}\s\d{4}\b"), "<aadhaar>"),
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{9}(?!\d)"), "<phone>"),
    (re.compile(r"(?<!\d)\d{9,18}(?!\d)"), "<number>"),
]


def redact(text: str) -> str:
    for pattern, replacement in _PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_value(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_value(v) for v in value]
    return value


# Attributes every LogRecord has; anything else was passed through `extra=` and is logged as a field.
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        request_id = request_id_var.get()
        if request_id:
            entry["request_id"] = request_id
        for key, value in vars(record).items():
            if key not in _STANDARD_ATTRS and not key.startswith("_"):
                entry[key] = _redact_value(value)
        if record.exc_info:
            entry["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(entry, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level)
    # httpx logs full URLs at INFO, which include UPI IDs in the path; the formatter redacts them anyway,
    # but there is no need for one line per bank call.
    logging.getLogger("httpx").setLevel(logging.WARNING)
