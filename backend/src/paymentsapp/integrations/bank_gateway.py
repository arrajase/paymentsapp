"""Provider interfaces the rest of the app depends on (plan 3.1, 9.5).

Business modules import only from this file, never from a specific adapter, so the mock bank can be replaced by
a partner bank, a PSP or an Account Aggregator without touching business logic. Amounts are integer paise.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

# ---- Domain types ----------------------------------------------------------------------------------------------


class TxnStatus(StrEnum):
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"


class Direction(StrEnum):
    DEBIT = "DEBIT"
    CREDIT = "CREDIT"


class CollectStatus(StrEnum):
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    DECLINED = "DECLINED"
    EXPIRED = "EXPIRED"

    @property
    def is_final(self) -> bool:
        return self is not CollectStatus.PENDING


class ConsentPurpose(StrEnum):
    LINK = "LINK"
    STATEMENT = "STATEMENT"
    BALANCE = "BALANCE"


class ConsentStatus(StrEnum):
    PENDING = "PENDING"
    ACTIVE = "ACTIVE"
    REJECTED = "REJECTED"
    REVOKED = "REVOKED"
    EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class VpaVerification:
    upi_id: str
    account_holder_name: str
    currency: str
    active: bool


@dataclass(frozen=True)
class Balance:
    upi_id: str
    amount_paise: int
    currency: str


@dataclass(frozen=True)
class BankTransaction:
    transaction_id: str
    status: TxnStatus
    failure_reason: str | None
    direction: Direction
    amount_paise: int
    currency: str
    upi_id: str | None
    counterparty_upi_id: str | None
    client_reference: str | None
    narration: str | None
    balance_after_paise: int | None
    created_at: datetime | None
    tran_date: date
    tran_type: str
    transfer_id: str | None = None


@dataclass(frozen=True)
class StatementPage:
    items: list[BankTransaction]
    next_cursor: str | None


@dataclass(frozen=True)
class CollectRequest:
    collect_id: str
    status: CollectStatus
    failure_reason: str | None
    payer_upi_id: str
    payee_upi_id: str
    amount_paise: int
    currency: str
    client_reference: str
    transaction_id: str | None
    expires_at: datetime


@dataclass(frozen=True)
class Consent:
    consent_id: str
    upi_id: str
    purposes: frozenset[ConsentPurpose]
    status: ConsentStatus
    statement_from: date | None
    statement_to: date | None
    expires_at: datetime


@dataclass(frozen=True)
class BankEvent:
    event_id: str
    type: str
    created_at: datetime
    data: dict[str, Any] = field(default_factory=dict)


# ---- Errors ----------------------------------------------------------------------------------------------------


class BankError(Exception):
    """The bank answered and the outcome is known: nothing happened, or it failed for `code`."""

    def __init__(self, code: str, message: str, *, retryable: bool = False, status_code: int | None = None):
        self.code = code
        self.message = message
        self.retryable = retryable
        self.status_code = status_code
        super().__init__(f"{code}: {message}")


class BankUnavailable(BankError):
    """The bank could not be reached or kept failing. For reads only: a money call raises OutcomeUnknown instead."""

    def __init__(self, message: str):
        super().__init__("BANK_UNAVAILABLE", message, retryable=True)


class OutcomeUnknown(Exception):
    """A money call may or may not have happened (timeout, 5xx after retries).

    The caller must mark the payment UNKNOWN, never FAILED, and settle it later with find_transaction (plan 9.4).
    """

    def __init__(self, client_reference: str, reason: str):
        self.client_reference = client_reference
        self.reason = reason
        super().__init__(f"outcome unknown for {client_reference}: {reason}")


class WebhookVerificationError(Exception):
    """A webhook failed signature, timestamp or format checks. Reject it; never process it."""


# ---- Interfaces ------------------------------------------------------------------------------------------------


@runtime_checkable
class PaymentProvider(Protocol):
    """UPI: check UPI IDs, prove ownership, take money with the customer's approval, pay out, look up status."""

    async def verify_vpa(self, upi_id: str) -> VpaVerification: ...

    async def request_consent(
        self,
        upi_id: str,
        purposes: set[ConsentPurpose],
        expires_at: datetime,
        statement_from: date | None = None,
        statement_to: date | None = None,
    ) -> Consent: ...

    async def get_consent(self, consent_id: str) -> Consent: ...

    async def list_consents(self, upi_id: str, status: ConsentStatus | None = None) -> list[Consent]: ...

    async def create_collect(
        self,
        payer_upi_id: str,
        payee_upi_id: str,
        amount_paise: int,
        client_reference: str,
        idempotency_key: str,
        note: str | None = None,
        expires_in_seconds: int = 300,
    ) -> CollectRequest: ...

    async def get_collect(self, collect_id: str) -> CollectRequest: ...

    async def pay(
        self,
        from_upi_id: str,
        to_upi_id: str,
        amount_paise: int,
        client_reference: str,
        idempotency_key: str,
        narration: str | None = None,
    ) -> BankTransaction: ...

    async def get_transaction(self, transaction_id: str) -> BankTransaction: ...

    async def find_transaction(self, client_reference: str) -> BankTransaction | None: ...


@runtime_checkable
class IssuerProvider(Protocol):
    """The bank holding escrow: balance and statement of the app's own accounts, for reconciliation (M4)."""

    async def get_balance(self, upi_id: str, consent_id: str | None = None) -> Balance: ...

    async def fetch_statement(
        self,
        upi_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
        consent_id: str | None = None,
    ) -> StatementPage: ...


@runtime_checkable
class AAProvider(Protocol):
    """Customer bank statements under consent (M10). The Account Aggregator in production."""

    async def fetch_statement(
        self,
        upi_id: str,
        *,
        date_from: date | None = None,
        date_to: date | None = None,
        cursor: str | None = None,
        limit: int = 100,
        consent_id: str | None = None,
    ) -> StatementPage: ...


@runtime_checkable
class BankEventSource(Protocol):
    """Events pushed by the bank (webhooks), plus polling to catch up on missed deliveries."""

    def verify_webhook(self, raw_body: bytes, headers: Mapping[str, str]) -> BankEvent: ...

    async def poll_events(self, after: str | None = None, limit: int = 100) -> list[BankEvent]: ...
