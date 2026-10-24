"""MockBankClient against a fake bank (httpx.MockTransport): retries, money-call safety, mapping."""

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from paymentsapp.integrations.bank_gateway import (
    BankError,
    BankUnavailable,
    CollectStatus,
    ConsentPurpose,
    ConsentStatus,
    Direction,
    IssuerProvider,
    OutcomeUnknown,
    PaymentProvider,
    TxnStatus,
)
from paymentsapp.integrations.mockbank.client import MockBankClient, RetryPolicy
from paymentsapp.integrations.mockbank.errors import CircuitBreaker

ESCROW = "paymentsappescrow@okmockbank"
CUSTOMER = "jane@okmockbank"


def txn(**overrides: Any) -> dict[str, Any]:
    record = {
        "transaction_id": "txn_1",
        "transfer_id": "trf_1",
        "status": "SUCCESS",
        "failure_reason": None,
        "tran_type": "Fund Transfer To",
        "posting_type": "Debit",
        "amount_cents": 12000,
        "currency": "INR",
        "upi_id": ESCROW,
        "counterparty_upi_id": CUSTOMER,
        "sender_upi_id": ESCROW,
        "recipient_upi_id": CUSTOMER,
        "client_reference": "pay_1",
        "narration": "Withdrawal",
        "balance_after_cents": 88000,
        "created_at": "2026-10-03T09:15:02.123456Z",
        "tran_date": "2026-10-03",
    }
    record.update(overrides)
    return record


def bank_error(code: str, retryable: bool = False, status: int = 422, **extra: Any) -> httpx.Response:
    return httpx.Response(
        status, json={"error": {"code": code, "message": code.lower(), "retryable": retryable}, **extra}
    )


class FakeBank:
    """Answers each request with the next queued response (or raises the queued exception) and records requests."""

    def __init__(self, *responses: httpx.Response | Exception | Callable[[httpx.Request], httpx.Response]):
        self.queue = list(responses)
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self.queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if callable(item) and not isinstance(item, httpx.Response):
            return item(request)
        return item


def make_client(bank: FakeBank, **kwargs: Any) -> tuple[MockBankClient, list[float]]:
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    client = MockBankClient(
        "https://bank.test",
        "mbk_test_key",
        webhook_secret="whsec",
        transport=httpx.MockTransport(bank),
        sleep=fake_sleep,
        retry=kwargs.pop("retry", RetryPolicy(max_attempts=3)),
        **kwargs,
    )
    return client, sleeps


async def test_satisfies_provider_interfaces() -> None:
    client, _ = make_client(FakeBank())
    assert isinstance(client, PaymentProvider)
    assert isinstance(client, IssuerProvider)
    await client.aclose()


async def test_pay_sends_contract_fields_and_maps_record() -> None:
    bank = FakeBank(httpx.Response(200, json=txn()))
    client, _ = make_client(bank)

    result = await client.pay(ESCROW, CUSTOMER, 12000, "pay_1", "idem-1", narration="Withdrawal")

    request = bank.requests[0]
    assert request.method == "POST"
    assert request.url.path == f"/upi/{ESCROW}/pay"
    assert request.headers["x-api-key"] == "mbk_test_key"
    assert request.headers["Idempotency-Key"] == "idem-1"
    assert json.loads(request.content) == {
        "recipient_upi_id": CUSTOMER,
        "amount_cents": 12000,
        "currency": "INR",
        "client_reference": "pay_1",
        "narration": "Withdrawal",
    }
    assert result.status is TxnStatus.SUCCESS
    assert result.direction is Direction.DEBIT
    assert result.amount_paise == 12000
    assert result.balance_after_paise == 88000
    assert result.created_at is not None and result.created_at.tzinfo is not None


async def test_retryable_error_is_retried_with_the_same_idempotency_key() -> None:
    bank = FakeBank(
        bank_error("SERVICE_UNAVAILABLE", retryable=True, status=503),
        bank_error("INTERNAL_ERROR", retryable=True, status=500),
        httpx.Response(200, json=txn(), headers={"Idempotent-Replayed": "true"}),
    )
    client, sleeps = make_client(bank)

    result = await client.pay(ESCROW, CUSTOMER, 12000, "pay_1", "idem-1")

    assert result.status is TxnStatus.SUCCESS
    assert [r.headers["Idempotency-Key"] for r in bank.requests] == ["idem-1"] * 3
    assert len({r.content for r in bank.requests}) == 1  # identical body, or the bank would answer 409
    assert len(sleeps) == 2


async def test_money_call_that_keeps_timing_out_is_unknown_not_failed() -> None:
    bank = FakeBank(*[httpx.ReadTimeout("slow")] * 3)
    client, _ = make_client(bank)

    with pytest.raises(OutcomeUnknown) as info:
        await client.pay(ESCROW, CUSTOMER, 12013, "pay_13", "idem-13")

    assert info.value.client_reference == "pay_13"
    assert len(bank.requests) == 3


async def test_money_call_with_5xx_after_retries_is_unknown() -> None:
    bank = FakeBank(*[bank_error("INTERNAL_ERROR", retryable=True, status=500)] * 3)
    client, _ = make_client(bank)

    with pytest.raises(OutcomeUnknown):
        await client.pay(ESCROW, CUSTOMER, 12014, "pay_14", "idem-14")


async def test_read_that_keeps_failing_is_bank_unavailable() -> None:
    bank = FakeBank(*[httpx.ConnectError("down")] * 3)
    client, _ = make_client(bank)

    with pytest.raises(BankUnavailable):
        await client.get_balance(ESCROW)


async def test_non_retryable_error_is_raised_immediately() -> None:
    bank = FakeBank(bank_error("NOT_PERMITTED", status=403))
    client, sleeps = make_client(bank)

    with pytest.raises(BankError) as info:
        await client.pay(CUSTOMER, ESCROW, 100, "pay_x", "idem-x")

    assert info.value.code == "NOT_PERMITTED"
    assert not isinstance(info.value, BankUnavailable)
    assert len(bank.requests) == 1
    assert sleeps == []


async def test_insufficient_funds_returns_the_recorded_failed_transaction() -> None:
    failed = txn(status="FAILED", failure_reason="INSUFFICIENT_FUNDS", balance_after_cents=None)
    bank = FakeBank(bank_error("INSUFFICIENT_FUNDS", transaction=failed))
    client, _ = make_client(bank)

    result = await client.pay(ESCROW, CUSTOMER, 999999, "pay_2", "idem-2")

    assert result.status is TxnStatus.FAILED
    assert result.failure_reason == "INSUFFICIENT_FUNDS"


async def test_retry_after_header_is_respected_and_capped() -> None:
    bank = FakeBank(
        httpx.Response(
            429,
            json={"error": {"code": "RATE_LIMITED", "message": "slow down", "retryable": True}},
            headers={"Retry-After": "7"},
        ),
        httpx.Response(
            429,
            json={"error": {"code": "RATE_LIMITED", "message": "slow down", "retryable": True}},
            headers={"Retry-After": "600"},
        ),
        httpx.Response(
            200, json={"upi_id": CUSTOMER, "account_holder_name": "Jane Doe", "currency": "INR", "status": "ACTIVE"}
        ),
    )
    client, sleeps = make_client(bank)

    result = await client.verify_vpa(CUSTOMER)

    assert sleeps == [7.0, 30.0]
    assert result.active and result.account_holder_name == "Jane Doe"


async def test_non_json_5xx_from_hosting_is_retryable() -> None:
    bank = FakeBank(
        httpx.Response(502, text="<html>Bad gateway</html>"),
        httpx.Response(
            200,
            json={
                "upi_id": ESCROW,
                "owner_name": "PaymentsApp",
                "account_type": "checking",
                "balance_cents": 5000,
                "currency": "INR",
            },
        ),
    )
    client, _ = make_client(bank)

    balance = await client.get_balance(ESCROW)

    assert balance.amount_paise == 5000


async def test_unreadable_success_on_money_call_is_unknown() -> None:
    bank = FakeBank(httpx.Response(200, json={"unexpected": True}))
    client, _ = make_client(bank)

    with pytest.raises(OutcomeUnknown):
        await client.pay(ESCROW, CUSTOMER, 100, "pay_3", "idem-3")


async def test_find_transaction_returns_none_when_bank_never_recorded_it() -> None:
    bank = FakeBank(bank_error("TRANSACTION_NOT_FOUND", status=404), httpx.Response(200, json=txn()))
    client, _ = make_client(bank)

    assert await client.find_transaction("pay_missing") is None
    found = await client.find_transaction("pay_1")

    assert found is not None and found.transaction_id == "txn_1"
    assert bank.requests[0].url.params["client_reference"] == "pay_missing"


async def test_statement_iterates_all_pages_and_omits_empty_params() -> None:
    bank = FakeBank(
        httpx.Response(200, json={"items": [txn(transaction_id="t1")], "next_cursor": "c2"}),
        httpx.Response(200, json={"items": [txn(transaction_id="t2")], "next_cursor": None}),
    )
    client, _ = make_client(bank)

    ids = [t.transaction_id async for t in client.iter_statement(ESCROW)]

    assert ids == ["t1", "t2"]
    assert "from" not in bank.requests[0].url.params
    assert bank.requests[1].url.params["cursor"] == "c2"


async def test_collect_and_consent_mapping() -> None:
    collect = {
        "collect_id": "col_1",
        "status": "PENDING",
        "failure_reason": None,
        "payer_upi_id": CUSTOMER,
        "payee_upi_id": ESCROW,
        "amount_cents": 50000,
        "currency": "INR",
        "client_reference": "topup_1",
        "note": "Add money",
        "transaction_id": None,
        "created_at": "2026-10-03T09:00:00Z",
        "updated_at": "2026-10-03T09:00:00Z",
        "expires_at": "2026-10-03T09:05:00Z",
    }
    consent = {
        "consent_id": "con_1",
        "upi_id": CUSTOMER,
        "purposes": ["LINK", "STATEMENT"],
        "statement_from": "2026-04-01",
        "statement_to": "2026-09-30",
        "status": "ACTIVE",
        "created_at": "2026-10-03T09:00:00Z",
        "updated_at": "2026-10-03T09:00:00Z",
        "expires_at": "2027-10-03T00:00:00Z",
    }
    bank = FakeBank(httpx.Response(201, json=collect), httpx.Response(200, json=[consent]))
    client, _ = make_client(bank)

    created = await client.create_collect(CUSTOMER, ESCROW, 50000, "topup_1", "idem-c")
    consents = await client.list_consents(CUSTOMER, ConsentStatus.ACTIVE)

    assert created.status is CollectStatus.PENDING and not created.status.is_final
    assert bank.requests[0].headers["Idempotency-Key"] == "idem-c"
    assert consents[0].purposes == {ConsentPurpose.LINK, ConsentPurpose.STATEMENT}
    assert bank.requests[1].url.params["status"] == "ACTIVE"


async def test_open_circuit_fails_fast_without_calling_the_bank() -> None:
    breaker = CircuitBreaker(failure_threshold=3, reset_after=30.0, clock=lambda: 0.0)
    bank = FakeBank(*[httpx.ConnectError("down")] * 3)
    client, _ = make_client(bank, breaker=breaker)

    with pytest.raises(BankUnavailable):
        await client.get_balance(ESCROW)
    assert breaker.state == "open"

    # A money call refused by an open circuit was never sent, so its outcome is known: not done.
    with pytest.raises(BankUnavailable):
        await client.pay(ESCROW, CUSTOMER, 100, "pay_4", "idem-4")
    assert len(bank.requests) == 3


def test_circuit_half_opens_after_reset_and_closes_on_success() -> None:
    now = [0.0]
    breaker = CircuitBreaker(failure_threshold=2, reset_after=10.0, clock=lambda: now[0])
    breaker.record_failure()
    breaker.record_failure()
    assert not breaker.allow()

    now[0] = 10.0
    assert breaker.state == "half-open" and breaker.allow()
    breaker.record_failure()  # the trial call failed: open again
    assert breaker.state == "open"

    now[0] = 20.0
    breaker.record_success()
    assert breaker.state == "closed"
