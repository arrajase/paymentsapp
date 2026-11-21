"""Wallet, ledger and adding money (plan M4, M5) through the customer API, with an in-memory bank in place of the
mock bank."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text, update
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from test_onboarding import Phone, set_control
from test_upi_linking import (
    MINE,
    WEBHOOK_SECRET,
    InMemoryBank,
    customer_with_kyc,
    link,
    links,
    signed,
)

from paymentsapp.api.v1 import webhooks
from paymentsapp.core.config import get_settings
from paymentsapp.db.session import get_session
from paymentsapp.integrations.bank_gateway import (
    BankError,
    BankTransaction,
    BankUnavailable,
    CollectRequest,
    CollectStatus,
    ConsentStatus,
    OutcomeUnknown,
)
from paymentsapp.integrations.devmessages import provider as dev_provider
from paymentsapp.integrations.mockbank.client import MockBankClient
from paymentsapp.main import create_app
from paymentsapp.modules.payments import service as payments_service
from paymentsapp.modules.payments.models import Payment, PaymentEvent
from paymentsapp.modules.plugins.models import PluginSetting
from paymentsapp.modules.plugins.service import seed_plugins
from paymentsapp.modules.upi import service as upi_service
from paymentsapp.modules.wallet import ledger
from paymentsapp.modules.wallet.models import JournalEntry, JournalLine, LedgerAccount

ESCROW = "escrow@okmockbank"


class CollectingBank(InMemoryBank):
    """Adds collect requests. A repeated idempotency key gets the stored request back, as the real bank does."""

    def __init__(self) -> None:
        super().__init__()
        self.collects: dict[str, CollectRequest] = {}
        self.by_key: dict[str, str] = {}
        self.lose_next_answer = False  # create the request, then act as if the answer never arrived
        self.down = False

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
        if self.down:
            raise BankUnavailable("circuit open")
        if idempotency_key not in self.by_key:
            collect = CollectRequest(
                collect_id=f"col_{uuid.uuid4().hex[:12]}",
                status=CollectStatus.PENDING,
                failure_reason=None,
                payer_upi_id=payer_upi_id,
                payee_upi_id=payee_upi_id,
                amount_paise=amount_paise,
                currency="INR",
                client_reference=client_reference,
                transaction_id=None,
                expires_at=datetime.now(UTC) + timedelta(seconds=expires_in_seconds),
            )
            self.collects[collect.collect_id] = collect
            self.by_key[idempotency_key] = collect.collect_id
        if self.lose_next_answer:
            self.lose_next_answer = False
            raise OutcomeUnknown(client_reference, "timeout")
        return self.collects[self.by_key[idempotency_key]]

    async def get_collect(self, collect_id: str) -> CollectRequest:
        return self.collects[collect_id]

    async def get_transaction(self, transaction_id: str) -> BankTransaction:
        # This bank can't say which transfer a leg belongs to; the transfer ID stays empty until reconciliation.
        raise BankError("TRANSACTION_NOT_FOUND", "not found", status_code=404)

    def resolve(self, status: CollectStatus, failure_reason: str | None = None) -> str:
        """The customer acts on the latest collect request at the bank."""
        collect = list(self.collects.values())[-1]
        resolved = CollectRequest(
            **{
                **collect.__dict__,
                "status": status,
                "failure_reason": failure_reason,
                "transaction_id": f"txn_{uuid.uuid4().hex[:12]}" if status == CollectStatus.SUCCESS else None,
            }
        )
        self.collects[collect.collect_id] = resolved
        return collect.collect_id


@pytest.fixture
async def client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    async def no_emulator(to: str, text: str) -> bool:
        return False

    monkeypatch.setattr(dev_provider, "push_sms_to_emulator", no_emulator)
    monkeypatch.setattr(get_settings(), "otp_resend_cooldown_seconds", 0)
    await seed_plugins(session)
    # Whatever the developer's .env says, the tests' bank has a known escrow account.
    setting = await session.get(PluginSetting, "mockbank")
    assert setting is not None
    setting.config = {**setting.config, "escrow_upi_id": ESCROW}
    await session.commit()

    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = test_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
async def bank(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[CollectingBank]:
    fake = CollectingBank()

    async def provider(_: AsyncSession) -> CollectingBank:
        return fake

    real = MockBankClient("https://bank.test", "mbk_test", webhook_secret=WEBHOOK_SECRET)

    async def event_source(_: AsyncSession) -> MockBankClient:
        return real

    monkeypatch.setattr(upi_service, "get_payment_provider", provider)
    monkeypatch.setattr(payments_service, "get_payment_provider", provider)
    monkeypatch.setattr(webhooks, "get_bank_event_source", event_source)
    yield fake
    await real.aclose()


async def linked_customer(
    client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank, **kwargs: Any
) -> Phone:
    phone = await customer_with_kyc(client, session, **kwargs)
    assert (await link(client, phone, MINE)).status_code == 201
    bank.set_status(MINE, ConsentStatus.ACTIVE)
    await links(client, phone)
    return phone


async def add_money(client: httpx.AsyncClient, phone: Phone, amount: int, key: str | None = None) -> httpx.Response:
    headers = {**phone.auth, "Idempotency-Key": key or str(uuid.uuid4())}
    return await client.post("/v1/wallet/add-money", json={"amount": amount}, headers=headers)


async def payment(client: httpx.AsyncClient, phone: Phone, payment_id: str) -> dict[str, Any]:
    response = await client.get(f"/v1/payments/{payment_id}", headers=phone.auth)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def wallet(client: httpx.AsyncClient, phone: Phone) -> dict[str, Any]:
    response = await client.get("/v1/wallet", headers=phone.auth)
    assert response.status_code == 200, response.text
    return dict(response.json())


async def entries_balance(session: AsyncSession) -> None:
    """Run the deferred balanced-entry check now; tests never really commit, so it would never fire otherwise."""
    await session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
    await session.execute(text("SET CONSTRAINTS ALL DEFERRED"))


# ---- Adding money ------------------------------------------------------------------------------------------------


async def test_add_money_end_to_end(client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank) -> None:
    phone = await linked_customer(client, session, bank)
    before = await wallet(client, phone)
    assert before["balance"] == 0 and before["tier"] == "T1" and before["can_add_now"] == 1_000_000

    created = await add_money(client, phone, 50_000)
    assert created.status_code == 201, created.text
    body = created.json()
    assert (
        body["status"] == "AWAITING_AUTH" and body["amount"] == 50_000 and body["counterparty"] == "te•••1@okmockbank"
    )
    collect = list(bank.collects.values())[0]
    assert collect.payer_upi_id == MINE and collect.payee_upi_id == ESCROW and collect.amount_paise == 50_000

    assert (await payment(client, phone, body["id"]))["status"] == "AWAITING_AUTH"
    assert (await wallet(client, phone))["can_add_now"] == 950_000  # the pending request counts against the cap

    bank.resolve(CollectStatus.SUCCESS)
    done = await payment(client, phone, body["id"])
    assert done["status"] == "SUCCESS" and done["completed_at"]
    assert (await payment(client, phone, body["id"]))["status"] == "SUCCESS"  # checking again changes nothing
    assert (await wallet(client, phone))["balance"] == 50_000

    escrow = await session.scalar(select(LedgerAccount).where(LedgerAccount.code == "escrow:mockbank"))
    assert escrow is not None and escrow.balance == 50_000
    entries = (
        await session.scalars(select(JournalEntry).where(JournalEntry.payment_id == uuid.UUID(body["id"])))
    ).all()
    assert len(entries) == 1 and entries[0].kind == "add_money"
    await entries_balance(session)

    events = (await session.scalars(select(PaymentEvent.to_status).order_by(PaymentEvent.id))).all()
    assert events == ["CHECKED", "AWAITING_AUTH", "SUCCESS"]
    history = (await client.get("/v1/wallet/transactions", headers=phone.auth)).json()
    assert [(p["kind"], p["status"]) for p in history] == [("add_money", "SUCCESS")]


async def test_declined_and_failed_requests_credit_nothing(
    client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank
) -> None:
    phone = await linked_customer(client, session, bank)
    declined = (await add_money(client, phone, 10_000)).json()
    bank.resolve(CollectStatus.DECLINED)
    result = await payment(client, phone, declined["id"])
    assert result["status"] == "FAILED" and result["failure_message"] == "You declined the request at your bank."

    broke = (await add_money(client, phone, 10_000)).json()
    bank.resolve(CollectStatus.FAILED, "INSUFFICIENT_FUNDS")
    assert (await payment(client, phone, broke["id"]))["failure_reason"] == "INSUFFICIENT_FUNDS"
    assert (await wallet(client, phone))["balance"] == 0

    bank.down = True
    unavailable = (await add_money(client, phone, 10_000)).json()
    assert unavailable["status"] == "FAILED" and unavailable["failure_reason"] == "BANK_UNAVAILABLE"


async def test_a_retried_request_never_adds_twice(
    client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank
) -> None:
    phone = await linked_customer(client, session, bank)
    key = str(uuid.uuid4())
    first = await add_money(client, phone, 20_000, key)
    again = await add_money(client, phone, 20_000, key)
    assert first.status_code == 201 and again.status_code == 201
    assert again.json()["id"] == first.json()["id"] and len(bank.collects) == 1

    different = await add_money(client, phone, 30_000, key)
    assert different.status_code == 409 and different.json()["error"]["code"] == "IDEMPOTENCY_KEY_REUSED"

    missing = await client.post("/v1/wallet/add-money", json={"amount": 100}, headers=phone.auth)
    assert missing.status_code == 422  # the Idempotency-Key header is required


async def test_a_lost_answer_is_unknown_then_settled(
    client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank
) -> None:
    phone = await linked_customer(client, session, bank)
    bank.lose_next_answer = True
    created = (await add_money(client, phone, 15_000)).json()
    assert created["status"] == "UNKNOWN"  # never FAILED: the bank did create the request

    # The worker's sweep repeats the request with the same key and picks up the bank's stored request.
    await session.execute(
        update(Payment)
        .where(Payment.id == uuid.UUID(created["id"]))
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    await session.commit()
    assert await payments_service.sweep(session) == 1
    assert (await payment(client, phone, created["id"]))["status"] == "AWAITING_AUTH"
    assert len(bank.collects) == 1

    bank.resolve(CollectStatus.SUCCESS)
    await session.execute(
        update(Payment)
        .where(Payment.id == uuid.UUID(created["id"]))
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    await session.commit()
    await payments_service.sweep(session)
    assert (await wallet(client, phone))["balance"] == 15_000


async def test_collect_webhook_settles_the_payment(
    client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank
) -> None:
    phone = await linked_customer(client, session, bank)
    created = (await add_money(client, phone, 25_000)).json()
    collect_id = bank.resolve(CollectStatus.SUCCESS)
    event = {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": "collect_request.updated",
        "created_at": datetime.now(UTC).isoformat(),
        "data": {"collect_id": collect_id, "status": "SUCCESS"},
    }
    raw, headers = signed(event)
    assert (await client.post("/v1/webhooks/mockbank", content=raw, headers=headers)).status_code == 200
    stored = await session.get(Payment, uuid.UUID(created["id"]), populate_existing=True)
    assert stored is not None and stored.status == "SUCCESS"
    assert (await wallet(client, phone))["balance"] == 25_000


# ---- Limits and prerequisites ------------------------------------------------------------------------------------


async def test_minimum_kyc_limits(client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank) -> None:
    phone = await linked_customer(client, session, bank)
    too_small = await add_money(client, phone, 50)
    assert too_small.status_code == 422 and too_small.json()["error"]["code"] == "AMOUNT_TOO_SMALL"
    over = await add_money(client, phone, 1_000_001)
    assert over.status_code == 422 and over.json()["error"]["code"] == "LIMIT_EXCEEDED"
    assert "₹10,000" in over.json()["error"]["message"]

    assert (await add_money(client, phone, 1_000_000)).status_code == 201  # up to the cap, still pending
    full = await add_money(client, phone, 100)
    assert full.status_code == 422 and full.json()["error"]["code"] == "LIMIT_REACHED"


async def test_new_device_cap(client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank) -> None:
    phone = await linked_customer(client, session, bank)
    from paymentsapp.modules.identity.models import Device

    await session.execute(
        update(Device)
        .where(Device.id == uuid.UUID(phone.device_id or ""))
        .values(cooling_until=datetime.now(UTC) + timedelta(hours=5))
    )
    await session.commit()
    capped = await add_money(client, phone, 600_000)
    assert capped.status_code == 422 and "new phone" in capped.json()["error"]["message"]


async def test_prerequisites(client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank) -> None:
    phone = await customer_with_kyc(client, session)
    no_link = await add_money(client, phone, 10_000)
    assert no_link.status_code == 409 and no_link.json()["error"]["code"] == "NO_LINKED_UPI"

    await set_control(session, "wallet", False)
    paused = await add_money(client, phone, 10_000)
    assert paused.status_code == 503 and paused.json()["error"]["code"] == "FEATURE_OFF"


async def test_payments_are_private(client: httpx.AsyncClient, session: AsyncSession, bank: CollectingBank) -> None:
    owner = await linked_customer(client, session, bank)
    created = (await add_money(client, owner, 10_000)).json()
    other = await customer_with_kyc(client, session, mobile="+91 90000 00002")
    response = await client.get(f"/v1/payments/{created['id']}", headers=other.auth)
    assert response.status_code == 404


# ---- Ledger rules ------------------------------------------------------------------------------------------------


async def test_ledger_refuses_unbalanced_or_overdrawn_entries(session: AsyncSession) -> None:
    escrow = await ledger.account(session, "escrow:test", "asset", provider="test")
    suspense = await ledger.account(session, "suspense:test", "liability")
    with pytest.raises(ledger.UnbalancedEntry):
        await ledger.post(
            session,
            kind="test",
            lines=[ledger.Line(escrow, "debit", 100), ledger.Line(suspense, "credit", 90)],
            description="unbalanced",
        )

    from paymentsapp.modules.identity.models import Customer

    customer = Customer(status="active", pin_failed_attempts=0)
    session.add(customer)
    await session.flush()
    wallet_account = await ledger.wallet_account(session, customer.id)
    with pytest.raises(ledger.InsufficientFunds):
        await ledger.post(
            session,
            kind="test",
            lines=[ledger.Line(wallet_account, "debit", 1), ledger.Line(suspense, "credit", 1)],
            description="overdraw",
        )
    await session.refresh(wallet_account)
    assert wallet_account.balance == 0  # nothing half-applied


async def test_written_entries_cannot_change(session: AsyncSession) -> None:
    escrow = await ledger.account(session, "escrow:test", "asset", provider="test")
    suspense = await ledger.account(session, "suspense:test", "liability")
    entry = await ledger.post(
        session,
        kind="test",
        lines=[ledger.Line(escrow, "debit", 100), ledger.Line(suspense, "credit", 100)],
        description="balanced",
    )
    await entries_balance(session)
    with pytest.raises(DBAPIError, match="append-only"):
        async with session.begin_nested():
            await session.execute(update(JournalLine).where(JournalLine.entry_id == entry.id).values(amount=1))


async def test_database_refuses_an_unbalanced_entry(engine: AsyncEngine) -> None:
    """Writing lines directly, past the service's own check, still can't commit an unbalanced entry."""
    async with engine.connect() as conn:
        outer = await conn.begin()
        try:
            account_id = uuid.uuid4()
            await conn.execute(
                text(
                    "INSERT INTO ledger_accounts (id, code, kind, currency, balance) "
                    "VALUES (:id, :code, 'asset', 'INR', 0)"
                ),
                {"id": account_id, "code": f"test:{account_id}"},
            )
            entry_id = await conn.scalar(
                text("INSERT INTO journal_entries (kind, description) VALUES ('test', 'raw') RETURNING id")
            )
            await conn.execute(
                text(
                    "INSERT INTO journal_lines (entry_id, account_id, direction, amount) VALUES (:e, :a, 'debit', 100)"
                ),
                {"e": entry_id, "a": account_id},
            )
            with pytest.raises(DBAPIError, match="does not balance"):
                await conn.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        finally:
            await outer.rollback()
