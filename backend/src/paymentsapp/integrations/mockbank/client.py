"""Mock bank adapter: implements PaymentProvider, IssuerProvider, AAProvider and BankEventSource (plan 9).

Retry rules (plan 9.4, 9.5):
- Only errors the bank marks `retryable`, timeouts and connection failures are retried, with backoff and jitter,
  respecting Retry-After.
- Money calls always resend the caller's original Idempotency-Key, so a retry can never move money twice.
- If a money call is still unresolved after the last attempt, it raises OutcomeUnknown, never a failure. The
  caller marks the payment UNKNOWN and settles it with find_transaction (or by repeating the same request with
  the same key, which the bank answers from its stored result).
- Reads that keep failing raise BankUnavailable.
"""

import asyncio
import logging
import random
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from types import TracebackType
from typing import Any, Self

import httpx
from pydantic import ValidationError

from paymentsapp.core.config import Settings
from paymentsapp.integrations.bank_gateway import (
    Balance,
    BankError,
    BankEvent,
    BankTransaction,
    BankUnavailable,
    CollectRequest,
    CollectStatus,
    Consent,
    ConsentPurpose,
    ConsentStatus,
    Direction,
    OutcomeUnknown,
    StatementPage,
    TxnStatus,
    VpaVerification,
)
from paymentsapp.integrations.mockbank import webhooks
from paymentsapp.integrations.mockbank.errors import CircuitBreaker, MockBankError, parse_error, retry_after_seconds
from paymentsapp.integrations.mockbank.schemas import (
    BalanceOut,
    CollectOut,
    ConsentOut,
    EventPageOut,
    StatementOut,
    TransactionOut,
    VerifyOut,
)

log = logging.getLogger(__name__)

CURRENCY = "INR"


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0
    max_retry_after: float = 30.0


class MockBankClient:
    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        webhook_secret: str | None = None,
        timeout: httpx.Timeout | None = None,
        retry: RetryPolicy | None = None,
        breaker: CircuitBreaker | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ):
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            headers={"x-api-key": api_key, "Accept": "application/json"},
            timeout=timeout or httpx.Timeout(20.0, connect=10.0),
            transport=transport,
        )
        self._webhook_secret = webhook_secret
        self._retry = retry or RetryPolicy()
        self.breaker = breaker or CircuitBreaker()
        self._sleep = sleep

    @classmethod
    def from_settings(cls, settings: Settings) -> Self:
        if settings.mockbank_api_key is None:
            raise RuntimeError("MOCKBANK_API_KEY is not set")
        secret = settings.mockbank_webhook_secret
        return cls(
            settings.mockbank_base_url,
            settings.mockbank_api_key.get_secret_value(),
            webhook_secret=secret.get_secret_value() if secret else None,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        await self.aclose()

    # ---- Transport ---------------------------------------------------------------------------------------------

    def _backoff(self, attempt: int, retry_after: float | None) -> float:
        if retry_after is not None:
            return min(retry_after, self._retry.max_retry_after)
        delay = min(self._retry.max_delay, self._retry.base_delay * float(2 ** (attempt - 1)))
        return delay * (0.5 + random.random() / 2)  # noqa: S311 - jitter, not security

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Mapping[str, Any] | None = None,
        idempotency_key: str | None = None,
        client_reference: str | None = None,
    ) -> Any:
        """Send with retries and return the decoded JSON body. A call with an idempotency key is a money call."""
        money_call = idempotency_key is not None
        if not self.breaker.allow():
            # Nothing was sent, so even a money call has a known outcome: it did not happen.
            raise BankUnavailable("circuit open: mock bank has been failing")

        headers = {"Idempotency-Key": idempotency_key} if idempotency_key else None
        query = {k: v for k, v in (params or {}).items() if v is not None}
        last_reason = "no attempt made"

        for attempt in range(1, self._retry.max_attempts + 1):
            retry_after: float | None = None
            try:
                response = await self._http.request(method, path, params=query, json=json, headers=headers)
            except httpx.TransportError as exc:  # timeouts and connection failures
                self.breaker.record_failure()
                last_reason = type(exc).__name__
            else:
                if response.is_success:
                    self.breaker.record_success()
                    try:
                        return response.json()
                    except ValueError:
                        if money_call:
                            raise OutcomeUnknown(client_reference or "", "unreadable success response") from None
                        raise BankError("UNEXPECTED_RESPONSE", "response was not JSON") from None

                error = parse_error(response)
                if response.status_code >= 500 or error.code == "RATE_LIMITED":
                    self.breaker.record_failure()
                else:
                    self.breaker.record_success()  # a 4xx means the bank is up and answered
                if not error.retryable:
                    raise error
                last_reason = error.code
                retry_after = retry_after_seconds(response)

            if attempt < self._retry.max_attempts:
                log.warning(
                    "mock bank call failed, retrying",
                    extra={"method": method, "path": path, "attempt": attempt, "reason": last_reason},
                )
                await self._sleep(self._backoff(attempt, retry_after))

        if money_call:
            raise OutcomeUnknown(client_reference or "", last_reason)
        raise BankUnavailable(f"{method} {path} failed after {self._retry.max_attempts} attempts: {last_reason}")

    async def warm_up(self, max_wait_seconds: float = 90.0) -> None:
        """Wake a sleeping free-tier deployment before real traffic (plan 9.5). Safe to call any time."""
        try:
            await self._http.get("/health", timeout=max_wait_seconds)
        except httpx.TransportError as exc:
            raise BankUnavailable(f"mock bank did not wake up: {type(exc).__name__}") from exc

    # ---- PaymentProvider ---------------------------------------------------------------------------------------

    async def verify_vpa(self, upi_id: str) -> VpaVerification:
        out = _parse(VerifyOut, await self._call("GET", f"/upi/{upi_id}/verify"))
        return VpaVerification(
            upi_id=out.upi_id,
            account_holder_name=out.account_holder_name,
            currency=out.currency,
            active=out.status == "ACTIVE",
        )

    async def request_consent(
        self,
        upi_id: str,
        purposes: set[ConsentPurpose],
        expires_at: datetime,
        statement_from: date | None = None,
        statement_to: date | None = None,
    ) -> Consent:
        body = {
            "upi_id": upi_id,
            "purposes": sorted(p.value for p in purposes),
            "expires_at": expires_at.isoformat(),
            "statement_from": statement_from.isoformat() if statement_from else None,
            "statement_to": statement_to.isoformat() if statement_to else None,
        }
        return _to_consent(_parse(ConsentOut, await self._call("POST", "/consents", json=body)))

    async def get_consent(self, consent_id: str) -> Consent:
        return _to_consent(_parse(ConsentOut, await self._call("GET", f"/consents/{consent_id}")))

    async def list_consents(self, upi_id: str, status: ConsentStatus | None = None) -> list[Consent]:
        params = {"upi_id": upi_id, "status": status.value if status else None}
        data = await self._call("GET", "/consents", params=params)
        if not isinstance(data, list):
            raise BankError("UNEXPECTED_RESPONSE", "consent list was not a list")
        return [_to_consent(_parse(ConsentOut, item)) for item in data]

    async def create_collect(
        self,
        payer_upi_id: str,
        payee_upi_id: str,
        amount_paise: int,
        client_reference: str,
        idempotency_key: str,
        note: str | None = None,
        expires_in_seconds: int = 300,
    ) -> CollectRequest:
        body = {
            "payer_upi_id": payer_upi_id,
            "payee_upi_id": payee_upi_id,
            "amount_cents": amount_paise,
            "currency": CURRENCY,
            "client_reference": client_reference,
            "note": note,
            "expires_in_seconds": expires_in_seconds,
        }
        data = await self._call(
            "POST", "/collect-requests", json=body, idempotency_key=idempotency_key, client_reference=client_reference
        )
        return _money_result(client_reference, lambda: _to_collect(_parse(CollectOut, data)))

    async def get_collect(self, collect_id: str) -> CollectRequest:
        return _to_collect(_parse(CollectOut, await self._call("GET", f"/collect-requests/{collect_id}")))

    async def pay(
        self,
        from_upi_id: str,
        to_upi_id: str,
        amount_paise: int,
        client_reference: str,
        idempotency_key: str,
        narration: str | None = None,
    ) -> BankTransaction:
        """Pay from one of the app's own UPI IDs. Insufficient funds returns a FAILED transaction, not an error."""
        body = {
            "recipient_upi_id": to_upi_id,
            "amount_cents": amount_paise,
            "currency": CURRENCY,
            "client_reference": client_reference,
            "narration": narration,
        }
        try:
            data = await self._call(
                "POST",
                f"/upi/{from_upi_id}/pay",
                json=body,
                idempotency_key=idempotency_key,
                client_reference=client_reference,
            )
        except MockBankError as exc:
            if exc.code == "INSUFFICIENT_FUNDS" and exc.transaction is not None:
                failed = exc.transaction
                return _money_result(client_reference, lambda: _to_transaction(_parse(TransactionOut, failed)))
            raise
        return _money_result(client_reference, lambda: _to_transaction(_parse(TransactionOut, data)))

    async def get_transaction(self, transaction_id: str) -> BankTransaction:
        return _to_transaction(_parse(TransactionOut, await self._call("GET", f"/transactions/{transaction_id}")))

    async def find_transaction(self, client_reference: str) -> BankTransaction | None:
        """Settle an UNKNOWN payment: the bank's record for this reference, or None if it never recorded one."""
        try:
            data = await self._call("GET", "/transactions", params={"client_reference": client_reference})
        except BankError as exc:
            if exc.code == "TRANSACTION_NOT_FOUND":
                return None
            raise
        return _to_transaction(_parse(TransactionOut, data))

    # ---- IssuerProvider / AAProvider ---------------------------------------------------------------------------

    async def get_balance(self, upi_id: str, consent_id: str | None = None) -> Balance:
        data = await self._call("GET", f"/upi/{upi_id}/balance", params={"consent_id": consent_id})
        out = _parse(BalanceOut, data)
        return Balance(upi_id=out.upi_id, amount_paise=out.balance_cents, currency=out.currency)

    async def fetch_statement(
        self,
        upi_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
        consent_id: str | None = None,
    ) -> StatementPage:
        params = {
            "from": date_from.isoformat() if date_from else None,
            "to": date_to.isoformat() if date_to else None,
            "cursor": cursor,
            "limit": limit,
            "consent_id": consent_id,
        }
        out = _parse(StatementOut, await self._call("GET", f"/upi/{upi_id}/statement", params=params))
        return StatementPage(items=[_to_transaction(t) for t in out.items], next_cursor=out.next_cursor)

    async def iter_statement(
        self,
        upi_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        consent_id: str | None = None,
        page_size: int = 500,
    ) -> AsyncIterator[BankTransaction]:
        """Every statement line in the range, newest first, following next_cursor."""
        cursor: str | None = None
        while True:
            page = await self.fetch_statement(
                upi_id, date_from=date_from, date_to=date_to, cursor=cursor, limit=page_size, consent_id=consent_id
            )
            for item in page.items:
                yield item
            if not page.next_cursor:
                return
            cursor = page.next_cursor

    # ---- BankEventSource ---------------------------------------------------------------------------------------

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> BankEvent:
        if not self._webhook_secret:
            raise RuntimeError("MOCKBANK_WEBHOOK_SECRET is not set")
        return webhooks.parse_webhook(self._webhook_secret, raw_body, headers)

    async def poll_events(self, after: str | None = None, limit: int = 100) -> list[BankEvent]:
        out = _parse(EventPageOut, await self._call("GET", "/events", params={"after": after, "limit": limit}))
        return [webhooks.to_event(e) for e in out.items]


# ---- Mapping from the wire format ------------------------------------------------------------------------------


def _parse[M](model: type[M], data: Any) -> M:
    try:
        return model.model_validate(data)  # type: ignore[attr-defined, no-any-return]
    except ValidationError as exc:
        raise BankError("UNEXPECTED_RESPONSE", f"{model.__name__} did not match the contract") from exc


def _money_result[T](client_reference: str, build: Callable[[], T]) -> T:
    """The bank accepted a money call but the reply can't be read: the outcome is unknown, not failed."""
    try:
        return build()
    except (BankError, ValueError) as exc:
        raise OutcomeUnknown(client_reference, "unreadable response to a money call") from exc


def _to_transaction(t: TransactionOut) -> BankTransaction:
    return BankTransaction(
        transaction_id=t.transaction_id,
        status=TxnStatus(t.status),
        failure_reason=t.failure_reason,
        direction=Direction.DEBIT if t.posting_type.lower() == "debit" else Direction.CREDIT,
        amount_paise=t.amount_cents,
        currency=t.currency or CURRENCY,
        upi_id=t.upi_id,
        counterparty_upi_id=t.counterparty_upi_id,
        client_reference=t.client_reference,
        narration=t.narration,
        balance_after_paise=t.balance_after_cents,
        created_at=t.created_at,
        tran_date=t.tran_date,
        tran_type=t.tran_type,
        transfer_id=t.transfer_id,
    )


def _to_collect(c: CollectOut) -> CollectRequest:
    return CollectRequest(
        collect_id=c.collect_id,
        status=CollectStatus(c.status),
        failure_reason=c.failure_reason,
        payer_upi_id=c.payer_upi_id,
        payee_upi_id=c.payee_upi_id,
        amount_paise=c.amount_cents,
        currency=c.currency,
        client_reference=c.client_reference,
        transaction_id=c.transaction_id,
        expires_at=c.expires_at,
    )


def _to_consent(c: ConsentOut) -> Consent:
    return Consent(
        consent_id=c.consent_id,
        upi_id=c.upi_id,
        purposes=frozenset(ConsentPurpose(p) for p in c.purposes),
        status=ConsentStatus(c.status),
        statement_from=c.statement_from,
        statement_to=c.statement_to,
        expires_at=c.expires_at,
    )
