"""Plugin registry and maker-checker (plan 3.1, M17), through the admin API against the test database."""

import json
from collections.abc import AsyncIterator

import httpx
import pyotp
import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Settings
from paymentsapp.db.session import get_session
from paymentsapp.main import create_app
from paymentsapp.modules.admin.service import AdminRole, create_admin
from paymentsapp.modules.audit.models import AuditEntry
from paymentsapp.modules.audit.service import verify_chain
from paymentsapp.modules.plugins.models import ChangeRequest, PluginSetting
from paymentsapp.modules.plugins.service import read_secret, seed_plugins

PASSWORD = "a long admin password"
SEEDED_KEY = "mbk_seed_0123456789abcdefghij"
NEW_KEY = "mbk_rotated_ZYXWVUTSRQPONMLKJIH"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = test_session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


@pytest.fixture
async def seeded(session: AsyncSession) -> None:
    settings = Settings(
        mockbank_base_url="https://bank.example.com",
        mockbank_api_key=SEEDED_KEY,
        mockbank_webhook_secret="whsec_seed_0123456789abcdef",
        mockbank_escrow_upi_id="escrow@okmockbank",
        mockbank_operating_upi_id="ops@okmockbank",
    )
    await seed_plugins(session, settings)
    await session.commit()


async def headers_for(client: httpx.AsyncClient, session: AsyncSession, login_id: str, *roles: AdminRole) -> dict:
    created = await create_admin(session, login_id, login_id, PASSWORD, set(roles))
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


async def test_seed_imports_env_values_once_and_hides_secrets(
    client: httpx.AsyncClient, session: AsyncSession, seeded: None, maker: dict
) -> None:
    assert await seed_plugins(session, Settings()) == []  # second run changes nothing

    plugins = {p["key"]: p for p in (await client.get("/admin/v1/plugins", headers=maker)).json()}
    assert set(plugins) == {"mockbank", "mock_kyc", "static_lists", "dev_messages"}
    bank = plugins["mockbank"]
    assert bank["enabled"] and bank["mode"] == "mock" and bank["allowed_modes"] == ["mock"]
    assert {f["name"]: f["value"] for f in bank["config"]}["escrow_upi_id"] == "escrow@okmockbank"
    secrets = {s["name"]: s for s in bank["secrets"]}
    assert secrets["api_key"]["preview"] == "mbk_…ghij"
    assert SEEDED_KEY not in json.dumps(plugins)
    assert await read_secret(session, "mockbank", "api_key") == SEEDED_KEY


async def test_change_needs_a_second_admin_and_is_audited(
    client: httpx.AsyncClient, session: AsyncSession, seeded: None, maker: dict, checker: dict
) -> None:
    proposal = {
        "reason": "rotate the bank key after the demo",
        "config": {"escrow_upi_id": "newescrow@okmockbank"},
        "secrets": {"api_key": NEW_KEY},
    }
    created = await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=maker)
    assert created.status_code == 201, created.text
    request = created.json()
    assert request["status"] == "PENDING"
    assert request["secrets"] == {"api_key": "mbk_…KJIH"}
    assert NEW_KEY not in created.text

    # Nothing changes until approved.
    assert await read_secret(session, "mockbank", "api_key") == SEEDED_KEY

    approved = await client.post(f"/admin/v1/change-requests/{request['id']}/approve", json={}, headers=checker)
    assert approved.status_code == 200, approved.text
    assert approved.json()["decided_by"]["login_id"] == "checker@paymentsapp.com"

    setting = await session.get(PluginSetting, "mockbank")
    assert setting is not None and setting.version == 2
    assert setting.config["escrow_upi_id"] == "newescrow@okmockbank"
    assert await read_secret(session, "mockbank", "api_key") == NEW_KEY
    stored = await session.get(ChangeRequest, request["id"])
    assert stored is not None and stored.secret_values == {}  # no leftover copy of the secret

    audit = (await session.scalars(select(AuditEntry).order_by(AuditEntry.id))).all()
    actions = [e.action for e in audit]
    assert "plugin.change_requested" in actions and "plugin.changed" in actions
    assert NEW_KEY not in json.dumps([e.after for e in audit] + [e.before for e in audit])
    assert (await verify_chain(session)).ok


async def test_roles_and_two_people_rule(
    client: httpx.AsyncClient, session: AsyncSession, seeded: None, maker: dict, checker: dict
) -> None:
    viewer = await headers_for(client, session, "viewer@paymentsapp.com", AdminRole.VIEWER)
    both = await headers_for(
        client, session, "both@paymentsapp.com", AdminRole.PLATFORM_ADMIN, AdminRole.SECURITY_APPROVER
    )
    proposal = {"reason": "point at the local mock bank", "config": {"base_url": "https://bank2.example.com"}}

    assert (
        await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=viewer)
    ).status_code == 403
    request = (await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=both)).json()

    # The proposer can't approve their own change, even holding the approver role; a maker can't approve at all.
    own = await client.post(f"/admin/v1/change-requests/{request['id']}/approve", json={}, headers=both)
    assert own.status_code == 403 and own.json()["error"]["code"] == "SAME_ADMIN"
    assert (
        await client.post(f"/admin/v1/change-requests/{request['id']}/approve", json={}, headers=maker)
    ).status_code == 403

    # Only one open request per plugin.
    second = await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=maker)
    assert second.status_code == 409 and second.json()["error"]["code"] == "CHANGE_PENDING"

    # Only the proposer can withdraw it.
    assert (await client.post(f"/admin/v1/change-requests/{request['id']}/cancel", headers=maker)).status_code == 403
    cancelled = await client.post(f"/admin/v1/change-requests/{request['id']}/cancel", headers=both)
    assert cancelled.json()["status"] == "CANCELLED"


@pytest.mark.parametrize(
    ("proposal", "message"),
    [
        ({"reason": "switch to production", "mode": "production"}, "not allowed in development"),
        ({"reason": "bad escrow id here", "config": {"escrow_upi_id": "not a upi id"}}, "UPI ID"),
        ({"reason": "plain http remote url", "config": {"base_url": "http://bank.example.com"}}, "https"),
        ({"reason": "unknown setting name", "config": {"colour": "blue"}}, "not a setting"),
        ({"reason": "unknown secret name", "secrets": {"password": "abcdefgh"}}, "not a secret"),
        ({"reason": "short", "enabled": False}, "reason"),
        ({"reason": "nothing actually changes", "enabled": True}, "nothing would change"),
    ],
    ids=["production-mode", "bad-upi", "http-url", "unknown-config", "unknown-secret", "short-reason", "no-op"],
)
async def test_invalid_changes_are_refused(
    client: httpx.AsyncClient, seeded: None, maker: dict, proposal: dict, message: str
) -> None:
    response = await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=maker)
    assert response.status_code == 422, response.text
    assert message in response.json()["error"]["message"]


async def test_reject_needs_a_reason_and_stale_requests_cannot_be_approved(
    client: httpx.AsyncClient, session: AsyncSession, seeded: None, maker: dict, checker: dict
) -> None:
    proposal = {"reason": "pause the plugin for maintenance", "enabled": False}
    request = (await client.post("/admin/v1/plugins/mockbank/change-requests", json=proposal, headers=maker)).json()

    no_reason = await client.post(f"/admin/v1/change-requests/{request['id']}/reject", json={}, headers=checker)
    assert no_reason.status_code == 422

    # Simulate the plugin changing after the request was made.
    await session.execute(update(PluginSetting).where(PluginSetting.key == "mockbank").values(version=99))
    await session.commit()
    stale = await client.post(f"/admin/v1/change-requests/{request['id']}/approve", json={}, headers=checker)
    assert stale.status_code == 409 and stale.json()["error"]["code"] == "STALE_CHANGE"

    rejected = await client.post(
        f"/admin/v1/change-requests/{request['id']}/reject",
        json={"note": "out of date, propose again"},
        headers=checker,
    )
    assert rejected.json()["status"] == "REJECTED"
    pending = await client.get("/admin/v1/change-requests", params={"status": "PENDING"}, headers=checker)
    assert pending.json() == []


async def test_audit_and_system_endpoints(client: httpx.AsyncClient, seeded: None, maker: dict) -> None:
    audit = (await client.get("/admin/v1/audit", params={"limit": 5}, headers=maker)).json()
    assert audit and audit[0]["id"] > audit[-1]["id"]  # newest first
    system = (await client.get("/admin/v1/system", headers=maker)).json()
    assert system["environment"] == "development"
    assert system["webhook_url"].endswith("/v1/webhooks/mockbank")


# ---- Building the bank adapter, and plugin health ----------------------------------------------------------------


async def test_bank_adapter_comes_from_the_registry(session: AsyncSession, seeded: None) -> None:
    from paymentsapp.integrations import registry

    await registry.close_clients()
    client = await registry.get_bank_client(session)
    assert str(client._http.base_url).rstrip("/") == "https://bank.example.com"
    assert await registry.get_bank_client(session) is client  # reused while the plugin version is unchanged

    await session.execute(update(PluginSetting).where(PluginSetting.key == "mockbank").values(enabled=False))
    await session.commit()
    with pytest.raises(registry.ProviderUnavailable):
        await registry.get_payment_provider(session)
    await registry.close_clients()


async def test_plugin_health(
    client: httpx.AsyncClient, session: AsyncSession, seeded: None, maker: dict, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paymentsapp.modules.plugins import health

    class HealthyBank:
        async def warm_up(self, max_wait_seconds: float = 90.0) -> None:
            return None

        async def poll_events(self, after: str | None = None, limit: int = 100) -> list[object]:
            return []

    async def bank(_: AsyncSession) -> HealthyBank:
        return HealthyBank()

    monkeypatch.setattr(health, "get_bank_client", bank)

    before = {p["key"]: p for p in (await client.get("/admin/v1/plugins/health", headers=maker)).json()}
    assert before["mockbank"]["remote"] and before["mockbank"]["last_check"] is None
    assert before["mockbank"]["events"]["received_24h"] == 0
    assert not before["mock_kyc"]["remote"] and before["mock_kyc"]["events"] is None

    checked = await client.post("/admin/v1/plugins/mockbank/health-check", headers=maker)
    assert checked.status_code == 200, checked.text
    assert checked.json()["ok"] is True and checked.json()["detail"] == "reachable, and the API key works"
    after = {p["key"]: p for p in (await client.get("/admin/v1/plugins/health", headers=maker)).json()}
    assert after["mockbank"]["last_check"]["ok"] is True

    in_process = await client.post("/admin/v1/plugins/mock_kyc/health-check", headers=maker)
    assert in_process.status_code == 409
    assert (await client.get("/admin/v1/plugins/health")).status_code == 401
