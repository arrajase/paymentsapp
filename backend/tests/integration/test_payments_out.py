"""Paying merchants, withdrawing, wallet-to-wallet transfers, reconciliation and the plugin money guard (plan M4,
M5), through the customer and admin APIs, with an in-memory bank in place of the mock bank."""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from test_onboarding import Phone
from test_plugins import headers_for
from test_upi_linking import MINE, WEBHOOK_SECRET, customer_with_kyc, step_up
from test_wallet import ESCROW, CollectingBank, add_money, entries_balance, linked_customer, payment, wallet

from paymentsapp.api.v1 import webhooks
from paymentsapp.core.config import get_settings
from paymentsapp.db.session import get_session
from paymentsapp.integrations import registry
from paymentsapp.integrations.bank_gateway import (
    Balance,
    BankError,
    BankTransaction,
    CollectStatus,
    Direction,
    OutcomeUnknown,
    StatementPage,
    TxnStatus,
)
from paymentsapp.integrations.devmessages import provider as dev_provider
from paymentsapp.integrations.devmessages.models import DevMessage
from paymentsapp.integrations.mockbank.client import MockBankClient
from paymentsapp.main import create_app
from paymentsapp.modules.admin.service import AdminRole
from paymentsapp.modules.compliance.limits import IST
from paymentsapp.modules.payments import service as payments_service
from paymentsapp.modules.payments.models import Payment
from paymentsapp.modules.plugins.models import PluginSetting
from paymentsapp.modules.plugins.service import seed_plugins
from paymentsapp.modules.upi import service as upi_service
from paymentsapp.modules.wallet import reconciliation
from paymentsapp.modules.wallet.models import JournalEntry, LedgerAccount

SHOP = "shop@okmockbank"


class Bank(CollectingBank):
    """Adds paying out from escrow, and the escrow account's balance and statement."""

    def __init__(self) -> None:
        super().__init__()
        self.accounts[SHOP] = ("Corner Shop", "INR")
        self.payouts: dict[str, BankTransaction] = {}  # by idempotency key
        self.escrow_lines: list[BankTransaction] = []
        self.legs: dict[str, BankTransaction] = {}  # every leg, by transaction ID
        self.pay_error: str | None = None  # the bank refuses the next payment with this code

    def _line(
        self,
        direction: Direction,
        amount: int,
        reference: str | None,
        counterparty: str,
        *,
        transfer_id: str | None = None,
        transaction_id: str | None = None,
        upi_id: str = ESCROW,
    ) -> BankTransaction:
        line = BankTransaction(
            transaction_id=transaction_id or f"txn_{uuid.uuid4().hex[:12]}",
            status=TxnStatus.SUCCESS,
            failure_reason=None,
            direction=direction,
            amount_paise=amount,
            currency="INR",
            upi_id=upi_id,
            counterparty_upi_id=counterparty,
            client_reference=reference,
            narration=None,
            balance_after_paise=None,
            created_at=datetime.now(UTC),
            tran_date=datetime.now(IST).date(),
            tran_type="test",
            transfer_id=transfer_id or f"trf_{uuid.uuid4().hex[:12]}",
        )
        self.legs[line.transaction_id] = line
        if upi_id == ESCROW:
            self.escrow_lines.append(line)
        return line

    def resolve(self, status: CollectStatus, failure_reason: str | None = None) -> str:
        collect_id = super().resolve(status, failure_reason)
        if status == CollectStatus.SUCCESS:
            # As at the mock bank: the payer's leg carries the reference, the escrow leg only the shared transfer ID.
            collect = self.collects[collect_id]
            transfer = f"trf_{uuid.uuid4().hex[:12]}"
            self._line(
                Direction.DEBIT,
                collect.amount_paise,
                collect.client_reference,
                ESCROW,
                transfer_id=transfer,
                transaction_id=collect.transaction_id,
                upi_id=collect.payer_upi_id,
            )
            self._line(Direction.CREDIT, collect.amount_paise, None, collect.payer_upi_id, transfer_id=transfer)
        return collect_id

    async def get_transaction(self, transaction_id: str) -> BankTransaction:
        return self.legs[transaction_id]

    async def pay(
        self,
        from_upi_id: str,
        to_upi_id: str,
        amount_paise: int,
        client_reference: str,
        idempotency_key: str,
        narration: str | None = None,
    ) -> BankTransaction:
        if idempotency_key not in self.payouts:
            if self.pay_error:
                code, self.pay_error = self.pay_error, None
                raise BankError(code, code.lower(), status_code=404)
            assert from_upi_id == ESCROW
            self.payouts[idempotency_key] = self._line(Direction.DEBIT, amount_paise, client_reference, to_upi_id)
        if self.lose_next_answer:
            self.lose_next_answer = False
            raise OutcomeUnknown(client_reference, "timeout")
        return self.payouts[idempotency_key]

    async def get_balance(self, upi_id: str, consent_id: str | None = None) -> Balance:
        total = sum(t.amount_paise if t.direction == Direction.CREDIT else -t.amount_paise for t in self.escrow_lines)
        return Balance(upi_id, total, "INR")

    async def fetch_statement(self, upi_id: str, **_: Any) -> StatementPage:
        return StatementPage(list(self.escrow_lines), None)


@pytest.fixture
async def client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    async def no_emulator(to: str, text: str) -> bool:
        return False

    monkeypatch.setattr(dev_provider, "push_sms_to_emulator", no_emulator)
    monkeypatch.setattr(get_settings(), "otp_resend_cooldown_seconds", 0)
    await seed_plugins(session)
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
async def bank(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[Bank]:
    fake = Bank()

    async def provider(_: AsyncSession) -> Bank:
        return fake

    real = MockBankClient("https://bank.test", "mbk_test", webhook_secret=WEBHOOK_SECRET)

    async def event_source(_: AsyncSession) -> MockBankClient:
        return real

    monkeypatch.setattr(upi_service, "get_payment_provider", provider)
    monkeypatch.setattr(payments_service, "get_payment_provider", provider)
    monkeypatch.setattr(reconciliation, "get_issuer", provider)
    monkeypatch.setattr(reconciliation, "get_payment_provider", provider)
    monkeypatch.setattr(webhooks, "get_bank_event_source", event_source)
    yield fake
    await real.aclose()


async def funded(
    client: httpx.AsyncClient, session: AsyncSession, bank: Bank, amount: int = 50_000, **kwargs: Any
) -> Phone:
    """A customer with a linked UPI ID and money in the wallet."""
    phone = await linked_customer(client, session, bank, **kwargs)
    created = (await add_money(client, phone, amount)).json()
    bank.resolve(CollectStatus.SUCCESS)
    assert (await payment(client, phone, created["id"]))["status"] == "SUCCESS"
    return phone


async def full_kyc(client: httpx.AsyncClient, phone: Phone) -> None:
    assert (await client.post("/v1/kyc/full", headers=phone.auth)).status_code == 200


async def pay(
    client: httpx.AsyncClient, phone: Phone, body: dict[str, Any], *, key: str | None = None, token: str | None = None
) -> httpx.Response:
    headers = {
        **phone.auth,
        "Idempotency-Key": key or str(uuid.uuid4()),
        "X-Step-Up-Token": token or await step_up(client, phone, "payment"),
    }
    return await client.post("/v1/payments", json=body, headers=headers)


async def balances(session: AsyncSession) -> dict[str, int]:
    accounts = (await session.scalars(select(LedgerAccount).execution_options(populate_existing=True))).all()
    return {a.code.split(":")[0]: a.balance for a in accounts if a.customer_id is None}


# ---- Paying a merchant -----------------------------------------------------------------------------------------


async def test_pay_a_merchant(client: httpx.AsyncClient, session: AsyncSession, bank: Bank) -> None:
    phone = await funded(client, session, bank)
    checked = await client.post("/v1/payments/payee", json={"upi_id": SHOP.upper()}, headers=phone.auth)
    assert checked.json() == {"upi_id": SHOP, "name_masked": "C••• S•••", "active": True}

    paid = await pay(client, phone, {"kind": "merchant", "amount": 20_000, "payee_upi_id": SHOP})
    assert paid.status_code == 201, paid.text
    assert paid.json()["status"] == "SUCCESS" and paid.json()["direction"] == "out"
    assert (await wallet(client, phone))["balance"] == 30_000
    assert await balances(session) == {"escrow": 30_000, "in_transit": 0}
    kinds = (
        await session.scalars(
            select(JournalEntry.kind)
            .where(JournalEntry.payment_id == uuid.UUID(paid.json()["id"]))
            .order_by(JournalEntry.id)
        )
    ).all()
    assert kinds == ["hold", "capture"]
    await entries_balance(session)
    alert = await session.scalar(
        select(DevMessage.body).where(DevMessage.purpose == "transaction_alert").order_by(DevMessage.id.desc())
    )
    assert alert is not None and "₹200 paid to shop@okmockbank" in alert


async def test_paying_needs_step_up_and_retries_are_safe(
    client: httpx.AsyncClient, session: AsyncSession, bank: Bank
) -> None:
    phone = await funded(client, session, bank)
    body = {"kind": "merchant", "amount": 10_000, "payee_upi_id": SHOP}
    no_step_up = await client.post(
        "/v1/payments", json=body, headers={**phone.auth, "Idempotency-Key": str(uuid.uuid4())}
    )
    assert no_step_up.status_code == 403 and no_step_up.json()["error"]["code"] == "STEP_UP_REQUIRED"

    key = str(uuid.uuid4())
    token = await step_up(client, phone, "payment")
    first = await pay(client, phone, body, key=key, token=token)
    again = await pay(client, phone, body, key=key, token=token)  # the token is used up, but this is a retry
    assert first.status_code == again.status_code == 201
    assert first.json()["id"] == again.json()["id"] and len(bank.payouts) == 1
    assert (await wallet(client, phone))["balance"] == 40_000


async def test_a_refused_payment_gives_the_money_back(
    client: httpx.AsyncClient, session: AsyncSession, bank: Bank
) -> None:
    phone = await funded(client, session, bank)
    bank.pay_error = "UPI_NOT_FOUND"
    refused = (
        await pay(client, phone, {"kind": "merchant", "amount": 20_000, "payee_upi_id": "gone@okmockbank"})
    ).json()
    assert refused["status"] == "FAILED" and "Nothing was taken" in refused["failure_message"]
    assert (await wallet(client, phone))["balance"] == 50_000
    assert await balances(session) == {"escrow": 50_000, "in_transit": 0}


async def test_a_lost_answer_stays_held_until_settled(
    client: httpx.AsyncClient, session: AsyncSession, bank: Bank
) -> None:
    phone = await funded(client, session, bank)
    bank.lose_next_answer = True
    lost = (await pay(client, phone, {"kind": "merchant", "amount": 20_000, "payee_upi_id": SHOP})).json()
    assert lost["status"] == "UNKNOWN"
    assert (await wallet(client, phone))["balance"] == 30_000  # held, not spent and not given back
    assert await balances(session) == {"escrow": 50_000, "in_transit": 20_000}

    await session.execute(
        update(Payment)
        .where(Payment.id == uuid.UUID(lost["id"]))
        .values(updated_at=datetime.now(UTC) - timedelta(minutes=5))
    )
    await session.commit()
    assert await payments_service.sweep(session) == 1
    assert (await payment(client, phone, lost["id"]))["status"] == "SUCCESS"
    assert len(bank.payouts) == 1  # the repeat used the same key, so the bank paid once
    assert await balances(session) == {"escrow": 30_000, "in_transit": 0}


async def test_cannot_spend_more_than_the_balance(client: httpx.AsyncClient, session: AsyncSession, bank: Bank) -> None:
    phone = await funded(client, session, bank, amount=10_000)
    over = await pay(client, phone, {"kind": "merchant", "amount": 10_001, "payee_upi_id": SHOP})
    assert over.status_code == 422 and over.json()["error"]["code"] == "INSUFFICIENT_FUNDS"
    assert await session.scalar(select(Payment.id).where(Payment.kind == "merchant")) is None
    own = await pay(client, phone, {"kind": "merchant", "amount": 100, "payee_upi_id": ESCROW})
    assert own.status_code == 422 and own.json()["error"]["code"] == "INVALID_PAYEE"


# ---- Withdrawing -----------------------------------------------------------------------------------------------


async def test_withdraw_needs_full_kyc(client: httpx.AsyncClient, session: AsyncSession, bank: Bank) -> None:
    phone = await funded(client, session, bank)
    refused = await pay(client, phone, {"kind": "withdrawal", "amount": 10_000})
    assert refused.status_code == 409 and refused.json()["error"]["code"] == "FULL_KYC_REQUIRED"

    await full_kyc(client, phone)
    done = await pay(client, phone, {"kind": "withdrawal", "amount": 10_000})
    assert done.status_code == 201 and done.json()["status"] == "SUCCESS"
    assert done.json()["counterparty"] == "te•••1@okmockbank"
    assert list(bank.payouts.values())[0].counterparty_upi_id == MINE
    assert (await wallet(client, phone))["balance"] == 40_000


# ---- Wallet to wallet ------------------------------------------------------------------------------------------


async def test_send_to_another_wallet(client: httpx.AsyncClient, session: AsyncSession, bank: Bank) -> None:
    sender = await funded(client, session, bank)
    await full_kyc(client, sender)
    receiver = await customer_with_kyc(client, session, mobile="+91 90000 00002", name="Ravi Kumar")

    found = await client.post("/v1/payments/recipient", json={"mobile": "9000000002"}, headers=sender.auth)
    assert found.json() == {"mobile_masked": "+91 90000 •••02", "name_masked": "R••• K•••", "can_receive": False}
    not_yet = await pay(client, sender, {"kind": "p2p", "amount": 10_000, "recipient_mobile": "9000000002"})
    assert not_yet.status_code == 409 and not_yet.json()["error"]["code"] == "RECIPIENT_CANT_RECEIVE"

    await full_kyc(client, receiver)
    sent = await pay(client, sender, {"kind": "p2p", "amount": 10_000, "recipient_mobile": "+91 90000 00002"})
    assert sent.status_code == 201 and sent.json()["status"] == "SUCCESS"
    assert (await wallet(client, sender))["balance"] == 40_000
    assert (await wallet(client, receiver))["balance"] == 10_000
    assert await balances(session) == {"escrow": 50_000}  # stayed in escrow: no bank call
    [received] = (await client.get("/v1/wallet/transactions", headers=receiver.auth)).json()
    assert received["direction"] == "in" and received["counterparty"] == "+91 90000 •••01"
    assert (await client.get(f"/v1/payments/{received['id']}", headers=receiver.auth)).status_code == 200

    self_send = await client.post("/v1/payments/recipient", json={"mobile": "9000000001"}, headers=sender.auth)
    assert self_send.status_code == 422
    nobody = await client.post("/v1/payments/recipient", json={"mobile": "9000000099"}, headers=sender.auth)
    assert nobody.status_code == 404


async def test_monthly_transfer_limit(client: httpx.AsyncClient, session: AsyncSession, bank: Bank) -> None:
    sender = await linked_customer(client, session, bank)
    await full_kyc(client, sender)
    created = (await add_money(client, sender, 1_500_000)).json()
    bank.resolve(CollectStatus.SUCCESS)
    await payment(client, sender, created["id"])
    receiver = await customer_with_kyc(client, session, mobile="+91 90000 00002")
    await full_kyc(client, receiver)

    over = await pay(client, sender, {"kind": "p2p", "amount": 1_000_001, "recipient_mobile": "9000000002"})
    assert over.status_code == 422 and "₹10,000" in over.json()["error"]["message"]


# ---- Reconciliation and the plugin guard -----------------------------------------------------------------------


async def test_reconciliation(client: httpx.AsyncClient, session: AsyncSession, bank: Bank) -> None:
    phone = await funded(client, session, bank)
    await pay(client, phone, {"kind": "merchant", "amount": 20_000, "payee_upi_id": SHOP})
    today = datetime.now(IST).date()

    clean = await reconciliation.reconcile(session, today, "test")
    assert clean.clean and clean.matched == 2 and clean.bank_balance == clean.ledger_balance == 30_000

    # Someone deposits into escrow at the bank directly: the ledger knows nothing about it.
    bank._line(Direction.CREDIT, 5_000, None, "stranger@okmockbank")
    dirty = await reconciliation.reconcile(session, today, "test")
    assert not dirty.clean
    assert {i["type"] for i in dirty.issues} == {"balance_mismatch", "bank_only"}

    admin = await headers_for(client, session, "ops@paymentsapp.com", AdminRole.OPERATIONS)
    overview = (await client.get("/admin/v1/reconciliation", headers=admin)).json()
    assert overview["totals"] == {"escrow": 30_000, "in_transit": 0, "wallets": 30_000}
    assert [r["clean"] for r in overview["runs"]] == [False, True]
    ran = await client.post("/admin/v1/reconciliation/run", json={}, headers=admin)
    assert ran.status_code == 201 and ran.json()["ran_by"] != "worker"


async def test_a_plugin_holding_money_cannot_be_disabled(
    client: httpx.AsyncClient, session: AsyncSession, bank: Bank
) -> None:
    await funded(client, session, bank)
    maker = await headers_for(client, session, "maker@paymentsapp.com", AdminRole.PLATFORM_ADMIN)
    proposal = {"reason": "switching the bank off for maintenance", "enabled": False}
    refused = await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=maker)
    assert refused.status_code == 422 and "holds customer money" in refused.json()["error"]["message"]
    moving = {"reason": "new escrow account at the bank", "config": {"escrow_upi_id": "new@okmockbank"}}
    refused = await client.post("/admin/v1/plugins/mockbank/change-requests", json=moving, headers=maker)
    assert refused.status_code == 422
    await registry.close_clients()
