"""Runtime controls and feature switches (plan 3.2, M17), through the admin API against the test database."""

from collections.abc import AsyncIterator
from datetime import timedelta

import httpx
import pyotp
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Environment, get_settings
from paymentsapp.db.session import get_session
from paymentsapp.main import create_app
from paymentsapp.modules.admin.service import AdminRole, create_admin
from paymentsapp.modules.audit.models import AuditEntry
from paymentsapp.modules.controls.models import ControlSetting
from paymentsapp.modules.controls.service import get_value

PASSWORD = "a long admin password"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = test_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def headers_for(client: httpx.AsyncClient, session: AsyncSession, login_id: str, role: AdminRole) -> dict:
    created = await create_admin(session, login_id, login_id, PASSWORD, {role})
    await session.commit()
    body = {"login_id": login_id, "password": PASSWORD, "totp_code": pyotp.TOTP(created.totp_secret).now()}
    response = await client.post("/admin/v1/auth/sign-in", json=body)
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['token']}"}


@pytest.fixture
async def maker(client: httpx.AsyncClient, session: AsyncSession) -> dict:
    return await headers_for(client, session, "maker@paymentsapp.com", AdminRole.PLATFORM_ADMIN)


@pytest.fixture
async def checker(client: httpx.AsyncClient, session: AsyncSession) -> dict:
    return await headers_for(client, session, "checker@paymentsapp.com", AdminRole.SECURITY_APPROVER)


async def change(client: httpx.AsyncClient, maker: dict, checker: dict, key: str, value: object) -> dict:
    proposed = await client.post(
        f"/admin/v1/controls/{key}/change-requests",
        json={"value": value, "reason": "test change for " + key},
        headers=maker,
    )
    assert proposed.status_code == 201, proposed.text
    approved = await client.post(f"/admin/v1/change-requests/{proposed.json()['id']}/approve", json={}, headers=checker)
    assert approved.status_code == 200, approved.text
    return dict(approved.json())


async def test_defaults_come_from_the_config_files(client: httpx.AsyncClient, maker: dict) -> None:
    controls = {c["key"]: c for c in (await client.get("/admin/v1/controls", headers=maker)).json()}
    assert controls["hardware_device_key"]["value"] == "off"  # development default: emulator software key
    assert controls["hardware_device_key"]["allowed"] == ["off", "enforce"]
    assert controls["otp_requests_per_hour"]["bounds"] == [1, 100]
    assert controls["wallet"]["kind"] == "feature" and controls["wallet"]["value"] is True
    assert all(c["source"] == "default" for c in controls.values())


async def test_change_applies_only_after_approval_and_is_audited(
    client: httpx.AsyncClient, session: AsyncSession, maker: dict, checker: dict
) -> None:
    proposed = await client.post(
        "/admin/v1/controls/device_integrity_detection/change-requests",
        json={"value": "off", "reason": "emulator testing without root checks"},
        headers=maker,
    )
    assert proposed.status_code == 201, proposed.text
    assert await get_value(session, "device_integrity_detection") == "monitor"  # unchanged until approved

    approved = await client.post(f"/admin/v1/change-requests/{proposed.json()['id']}/approve", json={}, headers=checker)
    assert approved.status_code == 200
    assert await get_value(session, "device_integrity_detection") == "off"

    actions = (await session.scalars(select(AuditEntry.action).order_by(AuditEntry.id))).all()
    assert "control.change_requested" in actions and "control.changed" in actions


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("app_attestation", "enforce", "must be one of"),  # development allows off or monitor only
        ("otp_requests_per_hour", 500, "between 1 and 100"),
        ("otp_requests_per_hour", "five", "whole number"),
        ("wallet", "maybe", "on or off"),
        ("hardware_device_key", "off", "nothing would change"),
    ],
    ids=["state-not-allowed", "number-out-of-bounds", "number-not-a-number", "feature-not-bool", "no-op"],
)
async def test_invalid_values_are_refused(
    client: httpx.AsyncClient, maker: dict, key: str, value: object, message: str
) -> None:
    response = await client.post(
        f"/admin/v1/controls/{key}/change-requests",
        json={"value": value, "reason": "testing invalid values"},
        headers=maker,
    )
    assert response.status_code == 422, response.text
    assert message in response.json()["error"]["message"]


async def test_feature_switch_and_app_config(
    client: httpx.AsyncClient, session: AsyncSession, maker: dict, checker: dict
) -> None:
    await change(client, maker, checker, "customer_signup", False)

    config = (await client.get("/v1/app-config")).json()  # public: no token
    assert config["controls"]["customer_signup"] is False
    assert config["controls"]["hardware_device_key"] == "off"
    assert "otp_requests_per_hour" not in config["controls"]  # server-side only


async def test_production_loosening_expires_and_reverts(
    client: httpx.AsyncClient, session: AsyncSession, maker: dict, checker: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(get_settings(), "environment", Environment.PRODUCTION)
    monkeypatch.setattr(
        get_settings(), "admin_ip_allowlist", ["127.0.0.0/8"]
    )  # production blocks all networks otherwise

    # Raising the OTP limit loosens security: it applies, but only for a limited time.
    approved = await change(client, maker, checker, "otp_requests_per_hour", 8)
    assert approved["status"] == "APPROVED"
    setting = await session.get(ControlSetting, "otp_requests_per_hour")
    assert setting is not None and setting.expires_at is not None and setting.revert_value == 5
    assert await get_value(session, "otp_requests_per_hour") == 8

    # Once the time passes, the previous value applies again with no one doing anything.
    await session.execute(
        update(ControlSetting)
        .where(ControlSetting.key == "otp_requests_per_hour")
        .values(expires_at=setting.expires_at - timedelta(hours=25))
    )
    await session.commit()
    session.expire_all()
    assert await get_value(session, "otp_requests_per_hour") == 5

    # Tightening does not expire.
    await change(client, maker, checker, "new_device_cooling_hours", 48)
    tightened = await session.get(ControlSetting, "new_device_cooling_hours")
    assert tightened is not None and tightened.expires_at is None

    # And production never offers off.
    off = await client.post(
        "/admin/v1/controls/certificate_pinning/change-requests",
        json={"value": "off", "reason": "trying to switch pinning off"},
        headers=maker,
    )
    assert off.status_code == 422


async def test_stored_value_no_longer_allowed_falls_back_to_default(session: AsyncSession) -> None:
    session.add(ControlSetting(key="otp_requests_per_hour", value=999, version=1, updated_by="test"))
    await session.commit()
    assert await get_value(session, "otp_requests_per_hour") == 50  # development default, not the stale 999
