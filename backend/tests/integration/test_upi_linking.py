"""UPI linking (plan M3) and step-up (plan M1) through the customer API, with an in-memory bank in place of the
mock bank. The real adapter is covered by the unit and contract tests."""

import json
import time
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from test_onboarding import PIN, Phone, set_control, sign_up

from paymentsapp.api.v1 import webhooks
from paymentsapp.core.config import get_settings
from paymentsapp.db.session import get_session
from paymentsapp.integrations.bank_gateway import BankError, Consent, ConsentPurpose, ConsentStatus, VpaVerification
from paymentsapp.integrations.devmessages import provider as dev_provider
from paymentsapp.integrations.devmessages.models import DevMessage
from paymentsapp.integrations.mockbank.client import MockBankClient
from paymentsapp.integrations.mockbank.signing import compute_signature
from paymentsapp.main import create_app
from paymentsapp.modules.audit.service import verify_chain
from paymentsapp.modules.plugins.service import seed_plugins
from paymentsapp.modules.upi import service as upi_service
from paymentsapp.modules.upi.models import UpiLink

MINE = "test1@okmockbank"
SECOND = "test.two@okmockbank"
NOT_ON_MOBILE = "elsewhere@okmockbank"
STRANGER = "someone@okmockbank"
WEBHOOK_SECRET = "whsec_test"


class InMemoryBank:
    """Just enough of PaymentProvider for linking. Consents start PENDING; tests approve or reject them."""

    def __init__(self) -> None:
        self.accounts = {
            MINE: ("test user", "INR"),
            SECOND: ("TEST USER", "INR"),
            NOT_ON_MOBILE: ("Test User", "INR"),
            STRANGER: ("Someone Else", "INR"),
            "dollars@okmockbank": ("Test User", "USD"),
        }
        self.mobiles = {"+919000000001": MINE}
        self.consents: dict[str, Consent] = {}

    async def verify_vpa(self, upi_id: str) -> VpaVerification:
        if upi_id not in self.accounts:
            raise BankError("UPI_NOT_FOUND", "no such UPI ID", status_code=404)
        name, currency = self.accounts[upi_id]
        return VpaVerification(upi_id, name, currency, active=True)

    async def lookup_mobile(self, mobile: str) -> VpaVerification | None:
        upi_id = self.mobiles.get(mobile)
        return await self.verify_vpa(upi_id) if upi_id else None

    async def request_consent(
        self, upi_id: str, purposes: set[ConsentPurpose], expires_at: datetime, *args: Any, **kwargs: Any
    ) -> Consent:
        consent = Consent(
            f"cns_{uuid.uuid4().hex[:12]}", upi_id, frozenset(purposes), ConsentStatus.PENDING, None, None, expires_at
        )
        self.consents[consent.consent_id] = consent
        return consent

    async def get_consent(self, consent_id: str) -> Consent:
        return self.consents[consent_id]

    def set_status(self, upi_id: str, status: ConsentStatus) -> str:
        consent = next(c for c in reversed(self.consents.values()) if c.upi_id == upi_id)
        self.consents[consent.consent_id] = Consent(
            consent.consent_id, upi_id, consent.purposes, status, None, None, consent.expires_at
        )
        return consent.consent_id


@pytest.fixture
async def client(session: AsyncSession, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[httpx.AsyncClient]:
    async def no_emulator(to: str, text: str) -> bool:
        return False

    monkeypatch.setattr(dev_provider, "push_sms_to_emulator", no_emulator)
    monkeypatch.setattr(get_settings(), "otp_resend_cooldown_seconds", 0)
    await seed_plugins(session)
    await session.commit()

    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = test_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
async def bank(monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[InMemoryBank]:
    fake = InMemoryBank()

    async def provider(_: AsyncSession) -> InMemoryBank:
        return fake

    real = MockBankClient("https://bank.test", "mbk_test", webhook_secret=WEBHOOK_SECRET)

    async def event_source(_: AsyncSession) -> MockBankClient:
        return real

    monkeypatch.setattr(upi_service, "get_payment_provider", provider)
    monkeypatch.setattr(webhooks, "get_bank_event_source", event_source)
    yield fake
    await real.aclose()


async def customer_with_kyc(
    client: httpx.AsyncClient, session: AsyncSession, mobile: str = "+91 90000 00001", name: str = "Test User"
) -> Phone:
    phone = await sign_up(client, session, "mobile", mobile)
    kyc = await client.post(
        "/v1/kyc/minimum", json={"full_name": name, "date_of_birth": "1990-01-01"}, headers=phone.auth
    )
    assert kyc.status_code == 200, kyc.text
    return phone


async def step_up(client: httpx.AsyncClient, phone: Phone, action: str, pin: str = PIN) -> str:
    challenge = await client.post("/v1/auth/step-up/challenge", json={"action": action}, headers=phone.auth)
    assert challenge.status_code == 200, challenge.text
    body = challenge.json()
    response = await client.post(
        "/v1/auth/step-up",
        json={"challenge_id": body["challenge_id"], "signature": phone.sign(body["sign"]), "pin": pin},
        headers=phone.auth,
    )
    assert response.status_code == 200, response.text
    return str(response.json()["step_up_token"])


async def link(client: httpx.AsyncClient, phone: Phone, upi_id: str) -> httpx.Response:
    token = await step_up(client, phone, "upi_link")
    return await client.post("/v1/upi/links", json={"upi_id": upi_id}, headers={**phone.auth, "X-Step-Up-Token": token})


async def links(client: httpx.AsyncClient, phone: Phone) -> list[dict[str, Any]]:
    response = await client.get("/v1/upi/links", headers=phone.auth)
    assert response.status_code == 200, response.text
    return list(response.json())


# ---- Finding and checking ----------------------------------------------------------------------------------------


async def test_discover_by_verified_mobile(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session)
    found = (await client.get("/v1/upi/discover", headers=phone.auth)).json()
    assert found["found"] is True
    candidate = found["candidate"]
    assert candidate["upi_id"] == MINE and candidate["holder_name_masked"] == "T••• U•••"
    assert candidate["name_matches"] and candidate["found_by_mobile"] and candidate["can_link"]
    assert "test user" not in json.dumps(found).lower()  # the full name never leaves the backend

    bank.mobiles.clear()
    assert (await client.get("/v1/upi/discover", headers=phone.auth)).json() == {"found": False, "candidate": None}


async def test_checks_on_a_typed_upi_id(client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank) -> None:
    phone = await customer_with_kyc(client, session)

    async def check(upi_id: str) -> httpx.Response:
        return await client.post("/v1/upi/validate", json={"upi_id": upi_id}, headers=phone.auth)

    assert (await check(STRANGER)).json()["problem_code"] == "NAME_MISMATCH"
    assert (await check("dollars@okmockbank")).json()["problem_code"] == "CURRENCY_NOT_SUPPORTED"
    assert (await check("nobody@okmockbank")).status_code == 404
    assert (await check("not a upi id")).status_code == 422

    # sim_binding off (the development default): any UPI ID in the customer's name can be linked.
    assert (await check(NOT_ON_MOBILE)).json()["can_link"] is True
    await set_control(session, "sim_binding", "enforce")
    enforced = (await check(NOT_ON_MOBILE)).json()
    assert enforced["can_link"] is False and enforced["problem_code"] == "NOT_ON_YOUR_MOBILE"
    assert (await check(MINE.upper())).json()["can_link"] is True


async def test_upi_checks_are_rate_limited(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session)
    await set_control(session, "vpa_verifications_per_hour", 2)
    for _ in range(2):
        ok = await client.post("/v1/upi/validate", json={"upi_id": STRANGER}, headers=phone.auth)
        assert ok.status_code == 200
    limited = await client.post("/v1/upi/validate", json={"upi_id": STRANGER}, headers=phone.auth)
    assert limited.status_code == 429 and int(limited.headers["Retry-After"]) > 0


async def test_prerequisites(client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank) -> None:
    phone = await sign_up(client, session, "mobile", "+91 90000 00001")
    no_kyc = await client.get("/v1/upi/discover", headers=phone.auth)
    assert no_kyc.status_code == 409 and no_kyc.json()["error"]["code"] == "KYC_REQUIRED"

    await client.post(
        "/v1/kyc/minimum", json={"full_name": "Test User", "date_of_birth": "1990-01-01"}, headers=phone.auth
    )
    await set_control(session, "upi_linking", False)
    paused = await client.get("/v1/upi/discover", headers=phone.auth)
    assert paused.status_code == 503 and paused.json()["error"]["code"] == "FEATURE_OFF"


# ---- Step-up ---------------------------------------------------------------------------------------------------


async def test_linking_needs_a_fresh_step_up_for_that_action(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session)

    async def attempt(token: str | None) -> httpx.Response:
        headers = {**phone.auth, **({"X-Step-Up-Token": token} if token else {})}
        return await client.post("/v1/upi/links", json={"upi_id": MINE}, headers=headers)

    missing = await attempt(None)
    assert missing.status_code == 403 and missing.json()["error"]["code"] == "STEP_UP_REQUIRED"
    wrong_action = await attempt(await step_up(client, phone, "upi_unlink"))
    assert wrong_action.status_code == 403

    token = await step_up(client, phone, "upi_link")
    assert (await attempt(token)).status_code == 201
    replayed = await attempt(token)
    assert replayed.status_code == 403  # used once


async def test_step_up_refuses_a_wrong_pin_or_signature(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session)
    challenge = (
        await client.post("/v1/auth/step-up/challenge", json={"action": "upi_link"}, headers=phone.auth)
    ).json()
    wrong_pin = await client.post(
        "/v1/auth/step-up",
        json={"challenge_id": challenge["challenge_id"], "signature": phone.sign(challenge["sign"]), "pin": "1357"},
        headers=phone.auth,
    )
    assert wrong_pin.status_code == 401 and wrong_pin.json()["error"]["code"] == "INVALID_PIN"

    challenge = (
        await client.post("/v1/auth/step-up/challenge", json={"action": "upi_link"}, headers=phone.auth)
    ).json()
    other_key = Phone.new()
    forged = await client.post(
        "/v1/auth/step-up",
        json={"challenge_id": challenge["challenge_id"], "signature": other_key.sign(challenge["sign"]), "pin": PIN},
        headers=phone.auth,
    )
    assert forged.status_code == 401 and forged.json()["error"]["code"] == "CHALLENGE_EXPIRED"

    # A step-up challenge can't be used to sign in.
    challenge = (
        await client.post("/v1/auth/step-up/challenge", json={"action": "upi_link"}, headers=phone.auth)
    ).json()
    sign_in = await client.post(
        "/v1/auth/sign-in",
        json={
            "device_id": phone.device_id,
            "challenge_id": challenge["challenge_id"],
            "signature": phone.sign(f"paymentsapp-sign-in:{challenge['challenge_id']}:{challenge['nonce']}"),
            "pin": PIN,
        },
    )
    assert sign_in.status_code == 401


# ---- Linking ---------------------------------------------------------------------------------------------------


async def test_link_approved_at_the_bank(client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank) -> None:
    phone = await customer_with_kyc(client, session)
    created = await link(client, phone, MINE)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["status"] == "pending" and body["upi_id"] == MINE and not body["is_default"]

    stored = await session.scalar(select(UpiLink).where(UpiLink.id == uuid.UUID(body["id"])))
    assert stored is not None and MINE not in stored.upi_id_encrypted  # encrypted at rest

    assert (await links(client, phone))[0]["status"] == "pending"
    bank.set_status(MINE, ConsentStatus.ACTIVE)
    [active] = await links(client, phone)
    assert active["status"] == "active" and active["is_default"] is True

    alert = await session.scalar(
        select(DevMessage).where(DevMessage.purpose == "security_alert").order_by(DevMessage.id.desc())
    )
    assert alert is not None and "te•••1@okmockbank was linked" in alert.body

    again = await link(client, phone, MINE)
    assert again.status_code == 409 and again.json()["error"]["code"] == "ALREADY_LINKED"
    assert (await verify_chain(session)).ok


async def test_a_rejected_or_unapproved_link_fails(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session)
    assert (await link(client, phone, MINE)).status_code == 201
    bank.set_status(MINE, ConsentStatus.REJECTED)
    [failed] = await links(client, phone)
    assert failed["status"] == "failed" and failed["status_reason"] == "rejected"

    assert (await link(client, phone, SECOND)).status_code == 201
    stale = await session.scalar(select(UpiLink).where(UpiLink.status == "pending"))
    assert stale is not None
    stale.created_at = datetime.now(UTC) - timedelta(hours=1)
    await session.commit()
    reasons = {item["upi_id"]: item["status_reason"] for item in await links(client, phone)}
    assert reasons[SECOND] == "approval_timeout"


async def test_name_mismatch_is_refused_and_recorded(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session, name="Asha Test")
    refused = await link(client, phone, MINE)
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "NAME_MISMATCH"
    assert await links(client, phone) == []


async def test_default_and_unlinking(client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank) -> None:
    phone = await customer_with_kyc(client, session)
    for upi_id in (MINE, SECOND):
        assert (await link(client, phone, upi_id)).status_code == 201
        bank.set_status(upi_id, ConsentStatus.ACTIVE)
        await links(client, phone)
    by_upi = {item["upi_id"]: item for item in await links(client, phone)}
    assert by_upi[MINE]["is_default"] and not by_upi[SECOND]["is_default"]

    second_id = by_upi[SECOND]["id"]
    no_step_up = await client.patch(f"/v1/upi/links/{second_id}", json={"is_default": True}, headers=phone.auth)
    assert no_step_up.status_code == 403
    token = await step_up(client, phone, "upi_set_default")
    changed = await client.patch(
        f"/v1/upi/links/{second_id}", json={"is_default": True}, headers={**phone.auth, "X-Step-Up-Token": token}
    )
    assert changed.status_code == 200 and changed.json()["is_default"] is True

    token = await step_up(client, phone, "upi_unlink")
    removed = await client.delete(f"/v1/upi/links/{second_id}", headers={**phone.auth, "X-Step-Up-Token": token})
    assert removed.status_code == 204
    [remaining] = await links(client, phone)
    assert remaining["upi_id"] == MINE and remaining["is_default"] is True  # the default moved back


async def test_customers_cannot_touch_each_others_links(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    owner = await customer_with_kyc(client, session)
    created = (await link(client, owner, MINE)).json()
    bank.set_status(MINE, ConsentStatus.ACTIVE)
    await links(client, owner)

    other = await customer_with_kyc(client, session, mobile="+91 90000 00002")
    token = await step_up(client, other, "upi_unlink")
    attempt = await client.delete(f"/v1/upi/links/{created['id']}", headers={**other.auth, "X-Step-Up-Token": token})
    assert attempt.status_code == 404
    assert await links(client, other) == []

    taken = await link(client, other, MINE)
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "UPI_ID_IN_USE"


async def test_link_limit(client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank) -> None:
    phone = await customer_with_kyc(client, session)
    extra = [f"test{i}@okmockbank" for i in range(2, 5)]
    for upi_id in extra:
        bank.accounts[upi_id] = ("Test User", "INR")
    for upi_id in extra:
        assert (await link(client, phone, upi_id)).status_code == 201
    over = await link(client, phone, MINE)
    assert over.status_code == 409 and over.json()["error"]["code"] == "LINK_LIMIT"


# ---- Webhooks --------------------------------------------------------------------------------------------------


def signed(event: dict[str, Any], secret: str = WEBHOOK_SECRET) -> tuple[bytes, dict[str, str]]:
    raw = json.dumps(event).encode()
    timestamp = int(time.time())
    return raw, {
        "X-MockBank-Signature": f"t={timestamp},v1={compute_signature(secret, timestamp, raw)}",
        "X-MockBank-Event-Id": event["event_id"],
        "Content-Type": "application/json",
    }


def consent_event(consent_id: str) -> dict[str, Any]:
    return {
        "event_id": f"evt_{uuid.uuid4().hex[:12]}",
        "type": "consent.updated",
        "created_at": datetime.now(UTC).isoformat(),
        "data": {"consent_id": consent_id, "status": "ACTIVE"},
    }


async def test_consent_webhook_activates_then_revokes(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    phone = await customer_with_kyc(client, session)
    assert (await link(client, phone, MINE)).status_code == 201
    consent_id = bank.set_status(MINE, ConsentStatus.ACTIVE)

    event = consent_event(consent_id)
    raw, headers = signed(event)
    delivered = await client.post("/v1/webhooks/mockbank", content=raw, headers=headers)
    assert delivered.status_code == 200 and delivered.json()["status"] == "ok"
    stored = await session.scalar(select(UpiLink).where(UpiLink.consent_id == consent_id))
    assert stored is not None and stored.status == "active"

    again = await client.post("/v1/webhooks/mockbank", content=raw, headers=headers)
    assert again.json()["status"] == "duplicate"

    bank.set_status(MINE, ConsentStatus.REVOKED)
    raw, headers = signed(consent_event(consent_id))
    assert (await client.post("/v1/webhooks/mockbank", content=raw, headers=headers)).status_code == 200
    await session.refresh(stored)
    assert stored.status == "removed" and stored.status_reason == "bank_revoked"
    assert await links(client, phone) == []


async def test_webhooks_must_be_signed(client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank) -> None:
    raw, headers = signed(consent_event("cns_x"), secret="not-the-secret")
    forged = await client.post("/v1/webhooks/mockbank", content=raw, headers=headers)
    assert forged.status_code == 401
    unsigned = await client.post("/v1/webhooks/mockbank", content=raw, headers={"Content-Type": "application/json"})
    assert unsigned.status_code == 401


async def test_a_consent_the_app_did_not_request_never_creates_a_link(
    client: httpx.AsyncClient, session: AsyncSession, bank: InMemoryBank
) -> None:
    # For example, the customer switched on App Access on the bank's profile page.
    raw, headers = signed(consent_event("cns_from_app_access"))
    assert (await client.post("/v1/webhooks/mockbank", content=raw, headers=headers)).status_code == 200
    assert await session.scalar(select(UpiLink.id)) is None
