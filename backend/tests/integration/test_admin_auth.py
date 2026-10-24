"""Admin sign-in, sessions, roles and the IP allowlist, through the real API against the test database."""

from collections.abc import AsyncIterator
from datetime import timedelta
from typing import Annotated

import httpx
import pyotp
import pytest
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.api.deps import require_roles
from paymentsapp.core.config import get_settings
from paymentsapp.db.session import get_session
from paymentsapp.main import create_app
from paymentsapp.modules.admin.models import AdminUser
from paymentsapp.modules.admin.service import AdminContext, AdminRole, create_admin
from paymentsapp.modules.audit.models import AuditEntry
from paymentsapp.modules.audit.service import verify_chain

PASSWORD = "a long admin password"


@pytest.fixture
async def client(session: AsyncSession) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app()

    async def test_session() -> AsyncIterator[AsyncSession]:
        yield session

    app.dependency_overrides[get_session] = test_session

    # A route only platform admins may call, to test role checks.
    probe = APIRouter(prefix="/admin/v1")

    @probe.post("/probe/platform-only")
    async def platform_only(
        admin: Annotated[AdminContext, Depends(require_roles(AdminRole.PLATFORM_ADMIN))],
    ) -> dict[str, str]:
        return {"ok": str(admin.user.id)}

    app.include_router(probe)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as http:
        yield http


async def new_admin(session: AsyncSession, login_id: str, *roles: AdminRole) -> str:
    created = await create_admin(session, login_id, "Test Admin", PASSWORD, set(roles) or {AdminRole.VIEWER})
    await session.commit()
    return created.totp_secret


def sign_in_body(login_id: str, secret: str, password: str = PASSWORD, code: str | None = None) -> dict[str, str]:
    return {"login_id": login_id, "password": password, "totp_code": code or pyotp.TOTP(secret).now()}


async def test_sign_in_then_me_then_sign_out(client: httpx.AsyncClient, session: AsyncSession) -> None:
    secret = await new_admin(session, "Ops.Lead@Example.com", AdminRole.PLATFORM_ADMIN)

    signed_in = await client.post("/admin/v1/auth/sign-in", json=sign_in_body("ops.lead@example.com", secret))
    assert signed_in.status_code == 200, signed_in.text
    token = signed_in.json()["token"]
    assert token.startswith("adm_")
    headers = {"Authorization": f"Bearer {token}"}

    me = await client.get("/admin/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json()["roles"] == ["platform_admin"]
    assert me.json()["login_id"] == "ops.lead@example.com"

    assert (await client.post("/admin/v1/auth/sign-out", headers=headers)).status_code == 204
    assert (await client.get("/admin/v1/me", headers=headers)).status_code == 401

    actions = (await session.scalars(select(AuditEntry.action).order_by(AuditEntry.id))).all()
    assert actions == ["admin.created", "admin.signed_in", "admin.signed_out"]
    assert (await verify_chain(session)).ok


async def test_every_failure_looks_the_same(client: httpx.AsyncClient, session: AsyncSession) -> None:
    secret = await new_admin(session, "a@example.com")

    attempts = [
        sign_in_body("a@example.com", secret, password="wrong password!!"),
        sign_in_body("nobody@example.com", secret),
        sign_in_body("a@example.com", secret, code="000000"),
    ]
    bodies = []
    for body in attempts:
        response = await client.post("/admin/v1/auth/sign-in", json=body)
        assert response.status_code == 401
        error = response.json()["error"]
        bodies.append((error["code"], error["message"]))
    assert len(set(bodies)) == 1
    assert bodies[0][0] == "INVALID_CREDENTIALS"


async def test_totp_code_cannot_be_replayed(client: httpx.AsyncClient, session: AsyncSession) -> None:
    secret = await new_admin(session, "b@example.com")
    body = sign_in_body("b@example.com", secret)

    assert (await client.post("/admin/v1/auth/sign-in", json=body)).status_code == 200
    assert (await client.post("/admin/v1/auth/sign-in", json=body)).status_code == 401


async def test_account_locks_after_repeated_failures(client: httpx.AsyncClient, session: AsyncSession) -> None:
    secret = await new_admin(session, "c@example.com")
    limit = get_settings().admin_max_failed_logins

    for _ in range(limit):
        bad = sign_in_body("c@example.com", secret, password="wrong password!!")
        assert (await client.post("/admin/v1/auth/sign-in", json=bad)).status_code == 401

    # Correct password and code, but the account is locked.
    assert (await client.post("/admin/v1/auth/sign-in", json=sign_in_body("c@example.com", secret))).status_code == 401
    user = await session.scalar(select(AdminUser).where(AdminUser.login_id == "c@example.com"))
    assert user is not None and user.locked_until is not None
    reasons = (await session.scalars(select(AuditEntry.after).where(AuditEntry.action == "admin.sign_in_failed"))).all()
    assert {"reason": "locked_now"} in reasons and {"reason": "locked"} in reasons


@pytest.mark.parametrize(
    "authorization",
    [None, "Bearer", "Basic abc", "Bearer cus_customer-token", "Bearer adm_not-a-real-token"],
    ids=["missing", "empty", "wrong-scheme", "customer-token", "unknown-admin-token"],
)
async def test_bad_tokens_are_rejected(client: httpx.AsyncClient, authorization: str | None) -> None:
    headers = {"Authorization": authorization} if authorization else {}
    response = await client.get("/admin/v1/me", headers=headers)
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


async def test_idle_session_expires(client: httpx.AsyncClient, session: AsyncSession) -> None:
    secret = await new_admin(session, "d@example.com")
    token = (await client.post("/admin/v1/auth/sign-in", json=sign_in_body("d@example.com", secret))).json()["token"]
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/admin/v1/me", headers=headers)).status_code == 200

    from paymentsapp.modules.admin.models import AdminSession

    admin_session = await session.scalar(select(AdminSession).where(AdminSession.revoked_at.is_(None)))
    assert admin_session is not None
    admin_session.last_seen_at -= timedelta(minutes=get_settings().admin_session_idle_minutes + 1)
    await session.commit()

    assert (await client.get("/admin/v1/me", headers=headers)).status_code == 401


async def test_roles_are_enforced(client: httpx.AsyncClient, session: AsyncSession) -> None:
    viewer = await new_admin(session, "viewer@example.com", AdminRole.VIEWER)
    maker = await new_admin(session, "maker@example.com", AdminRole.PLATFORM_ADMIN)

    async def token_for(login_id: str, secret: str) -> str:
        response = await client.post("/admin/v1/auth/sign-in", json=sign_in_body(login_id, secret))
        return str(response.json()["token"])

    viewer_headers = {"Authorization": f"Bearer {await token_for('viewer@example.com', viewer)}"}
    maker_headers = {"Authorization": f"Bearer {await token_for('maker@example.com', maker)}"}

    assert (await client.post("/admin/v1/probe/platform-only", headers=viewer_headers)).status_code == 403
    assert (await client.post("/admin/v1/probe/platform-only", headers=maker_headers)).status_code == 200


async def test_ip_allowlist_blocks_other_networks(client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "admin_ip_allowlist", ["10.0.0.0/8"])
    response = await client.post(
        "/admin/v1/auth/sign-in", json={"login_id": "x@x.io", "password": "x", "totp_code": "1"}
    )
    assert response.status_code == 403  # refused before the body is even validated

    monkeypatch.setattr(get_settings(), "admin_ip_allowlist", ["127.0.0.0/8"])
    assert (await client.get("/admin/v1/me")).status_code == 401  # allowed through, then needs a token


async def test_code_can_be_switched_off_in_development_only(
    client: httpx.AsyncClient, session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    from paymentsapp.core.config import Environment

    await new_admin(session, "demo@example.com")
    monkeypatch.setattr(get_settings(), "admin_totp_required", False)
    password_only = {"login_id": "demo@example.com", "password": PASSWORD}

    options = await client.get("/admin/v1/auth/options")
    assert options.json() == {"totp_required": False}
    signed_in = await client.post("/admin/v1/auth/sign-in", json=password_only)
    assert signed_in.status_code == 200, signed_in.text
    wrong = await client.post("/admin/v1/auth/sign-in", json={**password_only, "password": "wrong password!!"})
    assert wrong.status_code == 401

    audit = await session.scalar(
        select(AuditEntry.after).where(AuditEntry.action == "admin.signed_in").order_by(AuditEntry.id.desc())
    )
    assert audit == {"authenticator_code": "not required (development)"}

    # The same setting is ignored outside development: the code is always required there.
    monkeypatch.setattr(get_settings(), "environment", Environment.STAGING)
    assert (await client.get("/admin/v1/auth/options")).json() == {"totp_required": True}
    assert (await client.post("/admin/v1/auth/sign-in", json=password_only)).status_code == 401
