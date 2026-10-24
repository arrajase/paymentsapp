"""Contract tests against a real mock bank (plan 11). Run with: uv run pytest -m contract

Needs in backend/.env: MOCKBANK_API_KEY, MOCKBANK_ESCROW_UPI_ID, MOCKBANK_OPERATING_UPI_ID and
MOCKBANK_TEST_CUSTOMER_UPI_ID (an ordinary test customer). Read-only by default.

MOCKBANK_CONTRACT_MOVE_MONEY=true also runs a payment test. It pays 1 rupee from the *operating* account to the
test customer, never touching escrow, so the escrow balance still matches the wallet GLs.
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest

from paymentsapp.core.config import Settings
from paymentsapp.integrations.bank_gateway import BankError, ConsentStatus, TxnStatus
from paymentsapp.integrations.mockbank.client import MockBankClient

pytestmark = pytest.mark.contract

settings = Settings()
missing = [
    name
    for name, value in {
        "MOCKBANK_API_KEY": settings.mockbank_api_key,
        "MOCKBANK_ESCROW_UPI_ID": settings.mockbank_escrow_upi_id,
        "MOCKBANK_OPERATING_UPI_ID": settings.mockbank_operating_upi_id,
        "MOCKBANK_TEST_CUSTOMER_UPI_ID": settings.mockbank_test_customer_upi_id,
    }.items()
    if not value
]
if missing:
    pytest.skip(f"contract tests need {', '.join(missing)} in backend/.env", allow_module_level=True)

ESCROW = settings.mockbank_escrow_upi_id or ""
OPERATING = settings.mockbank_operating_upi_id or ""
CUSTOMER = settings.mockbank_test_customer_upi_id or ""


@pytest.fixture
async def bank() -> AsyncIterator[MockBankClient]:
    client = MockBankClient.from_settings(settings)
    await client.warm_up()
    yield client
    await client.aclose()


async def test_verify_returns_name_and_inr_without_balance(bank: MockBankClient) -> None:
    result = await bank.verify_vpa(CUSTOMER)
    assert result.active
    assert result.currency == "INR"
    assert result.account_holder_name


async def test_unknown_upi_id_is_upi_not_found(bank: MockBankClient) -> None:
    with pytest.raises(BankError) as info:
        await bank.verify_vpa(f"nobody{uuid.uuid4().hex[:8]}@okmockbank")
    assert info.value.code == "UPI_NOT_FOUND"


async def test_escrow_balance_and_statement_are_readable(bank: MockBankClient) -> None:
    balance = await bank.get_balance(ESCROW)
    assert balance.currency == "INR"
    page = await bank.fetch_statement(ESCROW, limit=5)
    assert len(page.items) <= 5
    for line in page.items:
        assert line.status is TxnStatus.SUCCESS
        assert line.created_at is not None


async def test_customer_statement_without_consent_is_refused_or_consented(bank: MockBankClient) -> None:
    consents = await bank.list_consents(CUSTOMER, ConsentStatus.ACTIVE)
    if consents:
        pytest.skip("the test customer has switched on App Access for this app")
    with pytest.raises(BankError) as info:
        await bank.fetch_statement(CUSTOMER)
    assert info.value.code == "CONSENT_REQUIRED"


async def test_paying_from_a_customer_upi_id_is_not_permitted(bank: MockBankClient) -> None:
    with pytest.raises(BankError) as info:
        await bank.pay(CUSTOMER, OPERATING, 100, f"ct-{uuid.uuid4().hex[:12]}", str(uuid.uuid4()))
    assert info.value.code == "NOT_PERMITTED"


async def test_unknown_reference_is_not_found(bank: MockBankClient) -> None:
    assert await bank.find_transaction(f"never-{uuid.uuid4().hex}") is None


async def test_events_can_be_polled(bank: MockBankClient) -> None:
    events = await bank.poll_events(limit=5)
    assert len(events) <= 5


@pytest.mark.skipif(os.environ.get("MOCKBANK_CONTRACT_MOVE_MONEY") != "true", reason="moves money; opt in")
async def test_pay_is_idempotent_and_findable_by_reference(bank: MockBankClient) -> None:
    reference = f"ct-{uuid.uuid4().hex[:12]}"
    key = str(uuid.uuid4())

    first = await bank.pay(OPERATING, CUSTOMER, 100, reference, key, narration="contract test")
    replay = await bank.pay(OPERATING, CUSTOMER, 100, reference, key, narration="contract test")
    found = await bank.find_transaction(reference)

    assert first.status is TxnStatus.SUCCESS
    assert replay.transaction_id == first.transaction_id
    assert found is not None and found.transaction_id == first.transaction_id
