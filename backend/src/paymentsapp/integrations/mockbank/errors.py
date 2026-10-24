"""Mock bank error parsing and the circuit breaker."""

import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import ValidationError

from paymentsapp.integrations.bank_gateway import BankError
from paymentsapp.integrations.mockbank.schemas import ErrorEnvelope


class MockBankError(BankError):
    """A BankError that may carry the FAILED transaction the bank recorded (422 INSUFFICIENT_FUNDS)."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool,
        status_code: int,
        transaction: dict[str, Any] | None = None,
    ):
        super().__init__(code, message, retryable=retryable, status_code=status_code)
        self.transaction = transaction


def parse_error(response: httpx.Response) -> MockBankError:
    """Build an error from the bank's {"error": {...}} body; fall back on the status code for anything else.

    A non-JSON 5xx (for example a hosting proxy page while the service wakes up) is treated as retryable.
    """
    try:
        envelope = ErrorEnvelope.model_validate(response.json())
    except (ValueError, ValidationError):
        server_side = response.status_code >= 500 or response.status_code == 429
        code = "RATE_LIMITED" if response.status_code == 429 else "SERVICE_UNAVAILABLE" if server_side else "HTTP_ERROR"
        return MockBankError(
            code, f"HTTP {response.status_code}", retryable=server_side, status_code=response.status_code
        )
    detail = envelope.error
    return MockBankError(
        detail.code,
        detail.message,
        retryable=detail.retryable,
        status_code=response.status_code,
        transaction=envelope.transaction,
    )


def retry_after_seconds(response: httpx.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        return None  # HTTP-date form is not used by the mock bank


class CircuitBreaker:
    """Stops calling a bank that keeps failing, so requests fail fast instead of piling up on timeouts.

    Closed: calls go through. After `failure_threshold` consecutive failures it opens and refuses calls for
    `reset_after` seconds, then lets one trial call through (half-open). Success closes it again.
    """

    def __init__(
        self, failure_threshold: int = 5, reset_after: float = 30.0, clock: Callable[[], float] = time.monotonic
    ):
        self.failure_threshold = failure_threshold
        self.reset_after = reset_after
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "half-open" if self._clock() - self._opened_at >= self.reset_after else "open"

    def allow(self) -> bool:
        return self.state != "open"

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self.state == "half-open" or self._failures >= self.failure_threshold:
            self._opened_at = self._clock()
