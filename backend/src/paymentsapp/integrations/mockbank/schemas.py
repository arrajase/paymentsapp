"""The mock bank's wire format (its README is the reference). Unknown fields are ignored so additions don't break us."""

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class _Wire(BaseModel):
    model_config = ConfigDict(extra="ignore")


class ErrorDetail(_Wire):
    code: str
    message: str = ""
    retryable: bool = False


class ErrorEnvelope(_Wire):
    error: ErrorDetail
    transaction: dict[str, Any] | None = None  # present on 422 INSUFFICIENT_FUNDS: a recorded FAILED transaction


class VerifyOut(_Wire):
    upi_id: str
    account_holder_name: str
    currency: str
    status: str


class BalanceOut(_Wire):
    upi_id: str
    balance_cents: int
    currency: str


class TransactionOut(_Wire):
    transaction_id: str
    transfer_id: str | None = None
    status: str
    failure_reason: str | None = None
    tran_type: str
    posting_type: str
    amount_cents: int
    currency: str | None = None
    upi_id: str | None = None
    counterparty_upi_id: str | None = None
    client_reference: str | None = None
    narration: str | None = None
    balance_after_cents: int | None = None
    created_at: datetime | None = None
    tran_date: date


class StatementOut(_Wire):
    items: list[TransactionOut]
    next_cursor: str | None = None


class CollectOut(_Wire):
    collect_id: str
    status: str
    failure_reason: str | None = None
    payer_upi_id: str
    payee_upi_id: str
    amount_cents: int
    currency: str
    client_reference: str
    transaction_id: str | None = None
    expires_at: datetime


class ConsentOut(_Wire):
    consent_id: str
    upi_id: str
    purposes: list[str]
    statement_from: date | None = None
    statement_to: date | None = None
    status: str
    expires_at: datetime


class EventOut(_Wire):
    event_id: str
    type: str
    created_at: datetime
    data: dict[str, Any]


class EventPageOut(_Wire):
    items: list[EventOut]
