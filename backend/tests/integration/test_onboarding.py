"""Customer onboarding (plan M1, M2 slim, M9, M13) through the customer API, against the test database.

OTPs are read from the dev inbox table, exactly as a developer reads them on the admin website.
"""

import base64
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import httpx
import pyotp
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import get_settings
from paymentsapp.db.session import get_session
from paymentsapp.integrations.devmessages import provider as dev_provider
from paymentsapp.integrations.devmessages.models import DevMessage
from paymentsapp.main import create_app
from paymentsapp.modules.admin.service import AdminRole, create_admin
from paymentsapp.modules.audit.service import verify_chain
from paymentsapp.modules.controls.models import ControlSetting
from paymentsapp.modules.identity.models import OtpChallenge
from paymentsapp.modules.plugins.models import PluginSetting
from paymentsapp.modules.plugins.service import seed_plugins

PIN = "2580"
MOBILE = "+91 90000 00001"


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


async def set_control(session: AsyncSession, key: str, value: Any) -> None:
    session.add(ControlSetting(key=key, value=value, version=1, updated_by="test"))
    await session.commit()


@dataclass
class Phone:
    """A test device: its key pair, and its tokens once signed in."""

    private_key: ec.EllipticCurvePrivateKey
    device_id: str | None = None
    access: str | None = None
    refresh: str | None = None

    @classmethod
    def new(cls) -> "Phone":
        return cls(ec.generate_private_key(ec.SECP256R1()))

    @property
    def public_key(self) -> str:
        der = self.private_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
        )
        return base64.b64encode(der).decode()

    def sign(self, text: str) -> str:
        return base64.b64encode(self.private_key.sign(text.encode(), ec.ECDSA(hashes.SHA256()))).decode()

    @property
    def auth(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access}"}


async def latest_code(session: AsyncSession) -> str:
    body = await session.scalar(
        select(DevMessage.body).where(DevMessage.purpose == "otp").order_by(DevMessage.id.desc())
    )
    assert body, "no OTP in the dev inbox"
    match = re.search(r"\b(\d{6})\b", body)
    assert match
    return match.group(1)


async def request_code(client: httpx.AsyncClient, kind: str, identifier: str) -> str:
    response = await client.post("/v1/auth/otp/request", json={"kind": kind, "identifier": identifier})
    assert response.status_code == 202, response.text
    return str(response.json()["challenge_id"])


async def verify(client: httpx.AsyncClient, session: AsyncSession, challenge_id: str) -> dict[str, Any]:
    response = await client.post(
        "/v1/auth/otp/verify", json={"challenge_id": challenge_id, "code": await latest_code(session)}
    )
    assert response.status_code == 200, response.text
    return dict(response.json())


async def enrol(client: httpx.AsyncClient, verified: dict[str, Any], phone: Phone, pin: str = PIN) -> httpx.Response:
    body = {
        "enrolment_token": verified["enrolment_token"],
        "public_key": phone.public_key,
        "platform": "android",
        "hardware_backed": False,
        "label": "Test emulator",
        "pin": pin,
        "accepted_terms_version": verified["terms"]["version"] if verified.get("terms") else None,
    }
    response = await client.post("/v1/auth/devices", json=body)
    if response.status_code == 201:
        data = response.json()
        phone.device_id = data["device_id"]
        phone.access = data["tokens"]["access_token"]
        phone.refresh = data["tokens"]["refresh_token"]
    return response


async def sign_up(
    client: httpx.AsyncClient, session: AsyncSession, kind: str = "mobile", identifier: str = MOBILE
) -> Phone:
    verified = await verify(client, session, await request_code(client, kind, identifier))
    assert verified["next_step"] == "create_account"
    phone = Phone.new()
    response = await enrol(client, verified, phone)
    assert response.status_code == 201, response.text
    return phone


async def device_sign_in(client: httpx.AsyncClient, phone: Phone, pin: str | None = PIN) -> httpx.Response:
    challenge = (await client.post("/v1/auth/challenge", json={"device_id": phone.device_id})).json()
    return await client.post(
        "/v1/auth/sign-in",
        json={
            "device_id": phone.device_id,
            "challenge_id": challenge["challenge_id"],
            "signature": phone.sign(challenge["sign"]),
            "pin": pin,
        },
    )


# ---- Sign-up --------------------------------------------------------------------------------------------------


async def test_sign_up_by_mobile(client: httpx.AsyncClient, session: AsyncSession) -> None:
    response = await client.post("/v1/auth/otp/request", json={"kind": "mobile", "identifier": MOBILE})
    assert response.status_code == 202
    assert response.json()["sent_to"] == "+91 90000 •••01"

    sms = await session.scalar(select(DevMessage).order_by(DevMessage.id.desc()))
    assert sms is not None and sms.channel == "sms" and sms.to_address == "+919000000001"

    phone = Phone.new()
    verified = await verify(client, session, response.json()["challenge_id"])
    assert verified["next_step"] == "create_account" and verified["terms"]["required"]
    enrolled = await enrol(client, verified, phone)
    assert enrolled.status_code == 201, enrolled.text
    assert enrolled.json()["new_customer"] is True

    me = (await client.get("/v1/me", headers=phone.auth)).json()
    assert me["identifiers"] == [
        {"kind": "mobile", "masked": "+91 90000 •••01", "verified_at": me["identifiers"][0]["verified_at"]}
    ]
    assert me["kyc"]["tier"] == "T0" and me["pin_set"] is True
    consents = (await client.get("/v1/consents", headers=phone.auth)).json()
    terms = next(c for c in consents if c["notice"]["purpose"] == "terms_and_privacy")
    assert terms["granted"] and not terms["needs_acceptance"]
    assert (await verify_chain(session)).ok


async def test_identifiers_are_never_stored_in_plain_text(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await sign_up(client, session)
    raw = await session.execute(select(OtpChallenge.value_encrypted, OtpChallenge.blind_index))
    for encrypted, index in raw.all():
        assert "9000000001" not in encrypted and "9000000001" not in index


async def test_request_answer_is_the_same_for_new_and_existing_accounts(
    client: httpx.AsyncClient, session: AsyncSession
) -> None:
    await sign_up(client, session)
    existing = await client.post("/v1/auth/otp/request", json={"kind": "mobile", "identifier": MOBILE})
    new = await client.post("/v1/auth/otp/request", json={"kind": "mobile", "identifier": "+91 90000 00002"})
    assert existing.status_code == new.status_code == 202
    assert set(existing.json()) == set(new.json())


async def test_terms_must_be_accepted_and_pin_must_be_strong(client: httpx.AsyncClient, session: AsyncSession) -> None:
    verified = await verify(client, session, await request_code(client, "mobile", MOBILE))
    no_terms = await enrol(client, {**verified, "terms": None}, Phone.new())
    assert no_terms.status_code == 422 and no_terms.json()["error"]["code"] == "TERMS_REQUIRED"
    weak = await enrol(client, verified, Phone.new(), pin="1234")
    assert weak.status_code == 422 and weak.json()["error"]["code"] == "WEAK_PIN"


async def test_sign_up_can_be_switched_off(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await set_control(session, "customer_signup", False)
    response = await client.post(
        "/v1/auth/otp/verify",
        json={"challenge_id": await request_code(client, "mobile", MOBILE), "code": await latest_code(session)},
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "SIGNUP_CLOSED"


async def test_hardware_key_rule_is_enforced_when_switched_on(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await set_control(session, "hardware_device_key", "enforce")
    verified = await verify(client, session, await request_code(client, "mobile", MOBILE))
    response = await enrol(client, verified, Phone.new())  # reports hardware_backed: false
    assert response.status_code == 403 and response.json()["error"]["code"] == "DEVICE_NOT_ALLOWED"


# ---- OTP abuse ------------------------------------------------------------------------------------------------


async def test_wrong_guesses_kill_the_code(client: httpx.AsyncClient, session: AsyncSession) -> None:
    challenge = await request_code(client, "mobile", MOBILE)
    right = await latest_code(session)
    wrong = "000000" if right != "000000" else "111111"
    for _ in range(get_settings().otp_max_attempts):
        response = await client.post("/v1/auth/otp/verify", json={"challenge_id": challenge, "code": wrong})
        assert response.status_code == 401
    late = await client.post("/v1/auth/otp/verify", json={"challenge_id": challenge, "code": right})
    assert late.status_code == 401 and late.json()["error"]["code"] == "INVALID_CODE"


async def test_codes_work_once_and_expire(client: httpx.AsyncClient, session: AsyncSession) -> None:
    challenge = await request_code(client, "mobile", MOBILE)
    code = await latest_code(session)
    assert (await client.post("/v1/auth/otp/verify", json={"challenge_id": challenge, "code": code})).status_code == 200
    assert (await client.post("/v1/auth/otp/verify", json={"challenge_id": challenge, "code": code})).status_code == 401

    expired = await request_code(client, "mobile", "+91 90000 00003")
    await session.execute(
        update(OtpChallenge)
        .where(OtpChallenge.id == expired)
        .values(expires_at=OtpChallenge.expires_at - timedelta(hours=1))
    )
    await session.commit()
    code = await latest_code(session)
    assert (await client.post("/v1/auth/otp/verify", json={"challenge_id": expired, "code": code})).status_code == 401


async def test_requests_are_rate_limited(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    await set_control(session, "otp_requests_per_hour", 2)
    for _ in range(2):
        await request_code(client, "mobile", MOBILE)
    third = await client.post("/v1/auth/otp/request", json={"kind": "mobile", "identifier": MOBILE})
    assert third.status_code == 429 and "Retry-After" in third.headers

    # Cooldown between resends.
    monkeypatch.setattr(get_settings(), "otp_resend_cooldown_seconds", 30)
    await request_code(client, "mobile", "+91 90000 00004")
    again = await client.post("/v1/auth/otp/request", json={"kind": "mobile", "identifier": "+91 90000 00004"})
    assert again.status_code == 429

    # Per IP: many different numbers from one address.
    monkeypatch.setattr(get_settings(), "otp_resend_cooldown_seconds", 0)
    monkeypatch.setattr(get_settings(), "otp_ip_hourly_multiplier", 1)
    blocked = await client.post("/v1/auth/otp/request", json={"kind": "mobile", "identifier": "+91 90000 00005"})
    assert blocked.status_code == 429


# ---- Returning sign-in, tokens and devices --------------------------------------------------------------------


async def test_returning_sign_in_with_device_signature(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session)
    signed_in = await device_sign_in(client, phone)
    assert signed_in.status_code == 200, signed_in.text

    # A signature from another key is refused, and a challenge works once.
    impostor = Phone.new()
    impostor.device_id = phone.device_id
    assert (await device_sign_in(client, impostor)).status_code == 401
    challenge = (await client.post("/v1/auth/challenge", json={"device_id": phone.device_id})).json()
    body = {
        "device_id": phone.device_id,
        "challenge_id": challenge["challenge_id"],
        "signature": phone.sign(challenge["sign"]),
        "pin": PIN,
    }
    assert (await client.post("/v1/auth/sign-in", json=body)).status_code == 200
    assert (await client.post("/v1/auth/sign-in", json=body)).status_code == 401


async def test_wrong_pins_lock_the_pin(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session)
    for _ in range(get_settings().pin_max_attempts):
        wrong = await device_sign_in(client, phone, pin="9753")
        assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "INVALID_PIN"
    locked = await device_sign_in(client, phone)
    assert locked.status_code == 423 and locked.json()["error"]["code"] == "PIN_LOCKED"


async def test_refresh_rotates_and_reuse_ends_the_session(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session)
    old_refresh = phone.refresh
    rotated = await client.post(
        "/v1/auth/token/refresh", json={"refresh_token": old_refresh, "device_id": phone.device_id}
    )
    assert rotated.status_code == 200
    new_access = rotated.json()["access_token"]
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {new_access}"})).status_code == 200

    # The old refresh token comes back: someone has a copy. The whole session ends.
    reused = await client.post(
        "/v1/auth/token/refresh", json={"refresh_token": old_refresh, "device_id": phone.device_id}
    )
    assert reused.status_code == 401
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {new_access}"})).status_code == 401


async def test_refresh_token_is_bound_to_its_device(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session)
    other = await client.post(
        "/v1/auth/token/refresh",
        json={"refresh_token": phone.refresh, "device_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert other.status_code == 401
    assert (await client.get("/v1/me", headers=phone.auth)).status_code == 401  # session revoked


async def test_new_device_replaces_the_old_one(client: httpx.AsyncClient, session: AsyncSession) -> None:
    old = await sign_up(client, session)
    await set_control(session, "new_device_cooling_hours", 24)

    verified = await verify(client, session, await request_code(client, "mobile", MOBILE))
    assert verified["next_step"] == "enter_pin"
    wrong = await enrol(client, verified, Phone.new(), pin="9753")
    assert wrong.status_code == 401 and wrong.json()["error"]["code"] == "INVALID_PIN"

    new = Phone.new()
    replaced = await enrol(client, verified, new)
    assert replaced.status_code == 201 and replaced.json()["replaced_device"] is True

    assert (await client.get("/v1/me", headers=old.auth)).status_code == 401
    assert (await client.post("/v1/auth/challenge", json={"device_id": old.device_id})).status_code == 401
    me = (await client.get("/v1/me", headers=new.auth)).json()
    assert me["device"]["cooling_until"] is not None
    alert = await session.scalar(select(DevMessage).where(DevMessage.purpose == "security_alert"))
    assert alert is not None and "new device" in alert.body


async def test_tokens_only_work_on_their_own_api(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session)
    created = await create_admin(session, "ops@paymentsapp.com", "Ops", "a long admin password", {AdminRole.VIEWER})
    await session.commit()
    admin_token = (
        await client.post(
            "/admin/v1/auth/sign-in",
            json={
                "login_id": "ops@paymentsapp.com",
                "password": "a long admin password",
                "totp_code": pyotp.TOTP(created.totp_secret).now(),
            },
        )
    ).json()["token"]

    assert (await client.get("/admin/v1/me", headers=phone.auth)).status_code == 401
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {admin_token}"})).status_code == 401
    assert (await client.get("/v1/me", headers={"Authorization": f"Bearer {phone.refresh}"})).status_code == 401
    assert (await client.get("/v1/me")).status_code == 401


async def test_sign_out(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session)
    assert (await client.post("/v1/auth/sign-out", headers=phone.auth)).status_code == 204
    assert (await client.get("/v1/me", headers=phone.auth)).status_code == 401


# ---- Second identifier, KYC and consents ------------------------------------------------------------------------


async def test_email_sign_up_then_mobile_then_kyc(client: httpx.AsyncClient, session: AsyncSession) -> None:
    phone = await sign_up(client, session, "email", "Asha.Test@example.com")
    email = await session.scalar(select(DevMessage).order_by(DevMessage.id.desc()).where(DevMessage.purpose == "otp"))
    assert email is not None and email.channel == "email"

    adult = {"full_name": "Asha  Test", "date_of_birth": "1995-06-15"}
    no_mobile = await client.post("/v1/kyc/minimum", json=adult, headers=phone.auth)
    assert no_mobile.status_code == 409 and no_mobile.json()["error"]["code"] == "MOBILE_REQUIRED"

    sent = await client.post(
        "/v1/me/identifiers/otp", json={"kind": "mobile", "identifier": MOBILE}, headers=phone.auth
    )
    assert sent.status_code == 202
    added = await client.post(
        "/v1/me/identifiers/verify",
        json={"challenge_id": sent.json()["challenge_id"], "code": await latest_code(session)},
        headers=phone.auth,
    )
    assert added.status_code == 201 and added.json()["masked"] == "+91 90000 •••01"

    today = date.today()
    minor = {"full_name": "Asha Test", "date_of_birth": today.replace(year=today.year - 17).isoformat()}
    too_young = await client.post("/v1/kyc/minimum", json=minor, headers=phone.auth)
    assert too_young.status_code == 422 and too_young.json()["error"]["code"] == "TOO_YOUNG"

    t1 = await client.post("/v1/kyc/minimum", json=adult, headers=phone.auth)
    assert t1.status_code == 200 and t1.json()["tier"] == "T1" and t1.json()["full_name"] == "Asha Test"

    # Full KYC pauses when no KYC plugin is enabled; it never upgrades without a check.
    await session.execute(update(PluginSetting).where(PluginSetting.key == "mock_kyc").values(enabled=False))
    await session.commit()
    paused = await client.post("/v1/kyc/full", headers=phone.auth)
    assert paused.status_code == 503
    await session.execute(update(PluginSetting).where(PluginSetting.key == "mock_kyc").values(enabled=True))
    await session.commit()
    t2 = await client.post("/v1/kyc/full", headers=phone.auth)
    assert t2.status_code == 200 and t2.json()["tier"] == "T2"


async def test_identifier_already_used_elsewhere(client: httpx.AsyncClient, session: AsyncSession) -> None:
    await sign_up(client, session, "mobile", MOBILE)
    other = await sign_up(client, session, "email", "ravi.test@example.com")
    sent = await client.post(
        "/v1/me/identifiers/otp", json={"kind": "mobile", "identifier": MOBILE}, headers=other.auth
    )
    taken = await client.post(
        "/v1/me/identifiers/verify",
        json={"challenge_id": sent.json()["challenge_id"], "code": await latest_code(session)},
        headers=other.auth,
    )
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "IDENTIFIER_IN_USE"


async def test_consents(client: httpx.AsyncClient, session: AsyncSession) -> None:
    notices = (await client.get("/v1/notices")).json()  # public
    insights = next(n for n in notices if n["purpose"] == "ai_insights")
    phone = await sign_up(client, session)

    granted = await client.post(
        "/v1/consents/ai_insights", json={"notice_version": insights["version"]}, headers=phone.auth
    )
    assert granted.status_code == 204
    stale = await client.post("/v1/consents/marketing", json={"notice_version": "0.0-old"}, headers=phone.auth)
    assert stale.status_code == 409
    assert (await client.delete("/v1/consents/ai_insights", headers=phone.auth)).status_code == 204
    required = await client.delete("/v1/consents/terms_and_privacy", headers=phone.auth)
    assert required.status_code == 409

    statuses = {
        c["notice"]["purpose"]: c["granted"] for c in (await client.get("/v1/consents", headers=phone.auth)).json()
    }
    assert statuses == {"terms_and_privacy": True, "ai_insights": False, "marketing": False}
