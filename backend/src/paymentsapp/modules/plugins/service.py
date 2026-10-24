"""Plugin registry and its maker-checker workflow (plan 3.1, M17).

Rules enforced here:
- A change is proposed by one admin and approved by a different one; both steps are audited.
- Modes and plugins are limited to what config/providers.yaml allows for this environment.
- At most one enabled plugin per interface.
- Secrets are write-only: stored encrypted, returned only as a preview, and never written to the audit log.
- Approving re-checks everything, and refuses if the plugin changed since the request was made.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Settings, get_settings
from paymentsapp.core.crypto import get_cipher
from paymentsapp.core.errors import AppError
from paymentsapp.modules.admin.models import AdminUser
from paymentsapp.modules.admin.service import AdminContext
from paymentsapp.modules.audit.service import SYSTEM, record
from paymentsapp.modules.plugins.catalogue import (
    Catalogue,
    PluginSpec,
    get_catalogue,
    validate_config_value,
    validate_secret_value,
)
from paymentsapp.modules.plugins.models import ChangeRequest, PluginSecret, PluginSetting

TARGET_PLUGIN = "plugin"
MIN_REASON_LENGTH = 10


def _now() -> datetime:
    return datetime.now(UTC)


def secret_preview(value: str) -> str:
    """Enough to recognise which secret is set, not enough to use it."""
    return f"{value[:4]}…{value[-4:]}" if len(value) >= 16 else "••••"


def _secret_context(plugin_key: str, name: str) -> str:
    return f"plugin_secrets:{plugin_key}:{name}"


def _proposal_context(request_id: uuid.UUID, name: str) -> str:
    return f"change_requests:{request_id}:{name}"


def _change_pending() -> AppError:
    return AppError("CHANGE_PENDING", "this plugin already has a change waiting for approval", status_code=409)


class InvalidChange(AppError):
    def __init__(self, message: str):
        super().__init__("INVALID_CHANGE", message, status_code=422)


# ---- Reading ---------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class SecretView:
    name: str
    label: str
    preview: str | None
    set_at: datetime | None
    set_by: str | None


@dataclass(frozen=True)
class PluginView:
    spec: PluginSpec
    setting: PluginSetting
    allowed_modes: tuple[str, ...]
    interfaces: tuple[str, ...]
    secrets: list[SecretView]
    pending_request_id: uuid.UUID | None


async def list_plugins(session: AsyncSession, catalogue: Catalogue | None = None) -> list[PluginView]:
    catalogue = catalogue or get_catalogue()
    settings = {s.key: s for s in (await session.scalars(select(PluginSetting))).all()}
    secrets: dict[tuple[str, str], PluginSecret] = {
        (s.plugin_key, s.name): s for s in (await session.scalars(select(PluginSecret))).all()
    }
    pending = {
        r.target_key: r.id
        for r in (
            await session.scalars(
                select(ChangeRequest).where(
                    ChangeRequest.target_type == TARGET_PLUGIN, ChangeRequest.status == "PENDING"
                )
            )
        ).all()
    }
    views = []
    for key, spec in catalogue.plugins.items():
        setting = settings.get(key)
        if setting is None:
            continue  # not seeded in this environment
        views.append(
            PluginView(
                spec=spec,
                setting=setting,
                allowed_modes=catalogue.allowed_modes(key),
                interfaces=catalogue.interfaces_served(key),
                secrets=[
                    SecretView(
                        name=f.name,
                        label=f.label,
                        preview=s.preview if (s := secrets.get((key, f.name))) else None,
                        set_at=s.set_at if s else None,
                        set_by=s.set_by if s else None,
                    )
                    for f in spec.secrets
                ],
                pending_request_id=pending.get(key),
            )
        )
    return views


async def read_secret(session: AsyncSession, plugin_key: str, name: str) -> str | None:
    """Decrypted secret for the backend's own use (building an adapter). Never return this to a client."""
    stored = await session.get(PluginSecret, (plugin_key, name))
    if stored is None:
        return None
    return get_cipher().decrypt(stored.value_encrypted, _secret_context(plugin_key, name))


# ---- Seeding ---------------------------------------------------------------------------------------------------


async def seed_plugins(session: AsyncSession, settings: Settings | None = None) -> list[str]:
    """Create a row for every plugin this environment allows, importing values from backend/.env once.

    Existing rows are left alone, so .env only matters the first time; afterwards the admin website is the source
    of truth. Returns the keys that were created.
    """
    settings = settings or get_settings()
    catalogue = get_catalogue()
    env_values = {name.upper(): value for name, value in settings.model_dump().items()}
    created = []
    for key, spec in catalogue.plugins.items():
        modes = catalogue.allowed_modes(key)
        if not modes or await session.get(PluginSetting, key) is not None:
            continue
        config = {
            f.name: str(env_values[f.env.upper()])
            for f in spec.config
            if f.env and env_values.get(f.env.upper()) not in (None, "")
        }
        setting = PluginSetting(key=key, mode=modes[0], enabled=True, config=config, version=1, updated_by="system")
        session.add(setting)
        await session.flush()
        imported_secrets = []
        for f in spec.secrets:
            raw = env_values.get(f.env.upper()) if f.env else None
            value = raw.get_secret_value() if isinstance(raw, SecretStr) else raw
            if value:
                session.add(
                    PluginSecret(
                        plugin_key=key,
                        name=f.name,
                        value_encrypted=get_cipher().encrypt(str(value), _secret_context(key, f.name)),
                        preview=secret_preview(str(value)),
                        set_at=_now(),
                        set_by="system",
                    )
                )
                imported_secrets.append(f.name)
        await record(
            session,
            SYSTEM,
            "plugin.seeded",
            TARGET_PLUGIN,
            key,
            after={"mode": setting.mode, "enabled": True, "config": config, "secrets_imported": imported_secrets},
        )
        created.append(key)
    await session.flush()
    return created


# ---- Proposing -------------------------------------------------------------------------------------------------


def _validate(
    catalogue: Catalogue,
    setting: PluginSetting,
    all_settings: list[PluginSetting],
    changes: dict[str, Any],
    secret_names: set[str],
) -> None:
    spec = catalogue.plugins.get(setting.key)
    if spec is None:
        raise InvalidChange("this plugin is no longer listed in providers.yaml")
    environment = get_settings().environment

    mode = changes.get("mode", setting.mode)
    allowed = catalogue.allowed_modes(setting.key)
    if mode not in allowed:
        raise InvalidChange(f"mode {mode!r} is not allowed in {environment.value}; allowed: {', '.join(allowed)}")

    if changes.get("enabled", setting.enabled):
        mine = set(catalogue.interfaces_served(setting.key))
        for other in all_settings:
            overlap = mine & set(catalogue.interfaces_served(other.key))
            if other.key != setting.key and other.enabled and overlap:
                raise InvalidChange(f"{other.key} already serves {', '.join(sorted(overlap))}; disable it first")

    for name, value in (changes.get("config") or {}).items():
        field = spec.config_field(name)
        if field is None:
            raise InvalidChange(f"{name!r} is not a setting of {setting.key}")
        problem = validate_config_value(field, value, environment)
        if problem:
            raise InvalidChange(f"{field.label} {problem}")

    for name in secret_names:
        if spec.secret_field(name) is None:
            raise InvalidChange(f"{name!r} is not a secret of {setting.key}")


async def propose_change(
    session: AsyncSession,
    admin: AdminContext,
    plugin_key: str,
    *,
    reason: str,
    enabled: bool | None = None,
    mode: str | None = None,
    config: dict[str, str] | None = None,
    secrets: dict[str, str] | None = None,
) -> ChangeRequest:
    setting = await session.get(PluginSetting, plugin_key, with_for_update=True)
    if setting is None:
        raise AppError("NOT_FOUND", "no such plugin", status_code=404)
    if len(reason.strip()) < MIN_REASON_LENGTH:
        raise InvalidChange(f"give a reason of at least {MIN_REASON_LENGTH} characters")

    changes: dict[str, Any] = {}
    if enabled is not None and enabled != setting.enabled:
        changes["enabled"] = enabled
    if mode is not None and mode != setting.mode:
        changes["mode"] = mode
    changed_config = {k: v for k, v in (config or {}).items() if setting.config.get(k) != v}
    if changed_config:
        changes["config"] = changed_config
    secrets = {k: v for k, v in (secrets or {}).items() if v}
    for name, value in secrets.items():
        problem = validate_secret_value(value)
        if problem:
            raise InvalidChange(f"secret {name} {problem}")
    if not changes and not secrets:
        raise InvalidChange("nothing would change")

    all_settings = list((await session.scalars(select(PluginSetting))).all())
    _validate(get_catalogue(), setting, all_settings, changes, set(secrets))

    already_pending = await session.scalar(
        select(ChangeRequest.id).where(
            ChangeRequest.target_type == TARGET_PLUGIN,
            ChangeRequest.target_key == plugin_key,
            ChangeRequest.status == "PENDING",
        )
    )
    if already_pending:
        raise _change_pending()

    request_id = uuid.uuid4()
    cipher = get_cipher()
    request = ChangeRequest(
        id=request_id,
        target_type=TARGET_PLUGIN,
        target_key=plugin_key,
        base_version=setting.version,
        changes=changes,
        secret_values={n: cipher.encrypt(v, _proposal_context(request_id, n)) for n, v in secrets.items()},
        secret_previews={n: secret_preview(v) for n, v in secrets.items()},
        reason=reason.strip(),
        status="PENDING",
        requested_by=admin.user.id,
    )
    session.add(request)
    try:
        await session.flush()
    except IntegrityError:
        # Another admin proposed a change at the same moment; the unique index caught it.
        await session.rollback()
        raise _change_pending() from None
    await record(
        session,
        admin.actor,
        "plugin.change_requested",
        TARGET_PLUGIN,
        plugin_key,
        after={"request_id": str(request_id), "changes": changes, "secrets": request.secret_previews, "reason": reason},
    )
    await session.commit()
    return request


# ---- Deciding --------------------------------------------------------------------------------------------------


async def _pending(session: AsyncSession, request_id: uuid.UUID) -> ChangeRequest:
    request = await session.get(ChangeRequest, request_id, with_for_update=True)
    if request is None:
        raise AppError("NOT_FOUND", "no such change request", status_code=404)
    if request.status != "PENDING":
        raise AppError("NOT_PENDING", f"this request is already {request.status.lower()}", status_code=409)
    return request


def _snapshot(setting: PluginSetting, secrets: dict[str, str]) -> dict[str, Any]:
    return {"enabled": setting.enabled, "mode": setting.mode, "config": dict(setting.config), "secrets": secrets}


async def approve(session: AsyncSession, admin: AdminContext, request_id: uuid.UUID, note: str | None) -> ChangeRequest:
    request = await _pending(session, request_id)
    if request.requested_by == admin.user.id:
        raise AppError("SAME_ADMIN", "a different admin must approve this change", status_code=403)
    setting = await session.get(PluginSetting, request.target_key, with_for_update=True)
    if setting is None:
        raise AppError("NOT_FOUND", "the plugin no longer exists", status_code=404)
    if setting.version != request.base_version:
        raise AppError("STALE_CHANGE", "the plugin changed after this request was made; reject it", status_code=409)

    all_settings = list((await session.scalars(select(PluginSetting))).all())
    _validate(get_catalogue(), setting, all_settings, request.changes, set(request.secret_values))

    existing = {
        s.name: s
        for s in (await session.scalars(select(PluginSecret).where(PluginSecret.plugin_key == setting.key))).all()
    }
    before = _snapshot(setting, {n: s.preview for n, s in existing.items()})

    now = _now()
    if "enabled" in request.changes:
        setting.enabled = bool(request.changes["enabled"])
    if "mode" in request.changes:
        setting.mode = str(request.changes["mode"])
    if "config" in request.changes:
        setting.config = {**setting.config, **request.changes["config"]}
    cipher = get_cipher()
    requester = str(request.requested_by)
    for name, proposed in request.secret_values.items():
        value = cipher.decrypt(proposed, _proposal_context(request.id, name))
        stored = existing.get(name)
        encrypted = cipher.encrypt(value, _secret_context(setting.key, name))
        if stored is None:
            stored = PluginSecret(plugin_key=setting.key, name=name)
            session.add(stored)
            existing[name] = stored
        stored.value_encrypted = encrypted
        stored.preview = request.secret_previews[name]
        stored.set_at = now
        stored.set_by = requester
    setting.version += 1
    setting.updated_at = now
    setting.updated_by = str(admin.user.id)

    request.status = "APPROVED"
    request.decided_by = admin.user.id
    request.decided_at = now
    request.decision_note = (note or "").strip() or None
    request.secret_values = {}
    await session.flush()

    after = _snapshot(setting, {n: s.preview for n, s in existing.items()})
    await record(
        session,
        admin.actor,
        "plugin.changed",
        TARGET_PLUGIN,
        setting.key,
        before=before,
        after={**after, "request_id": str(request.id), "requested_by": requester, "version": setting.version},
    )
    await session.commit()
    return request


async def reject(session: AsyncSession, admin: AdminContext, request_id: uuid.UUID, note: str) -> ChangeRequest:
    request = await _pending(session, request_id)
    if request.requested_by == admin.user.id:
        raise AppError("SAME_ADMIN", "withdraw your own request instead of rejecting it", status_code=403)
    if len(note.strip()) < MIN_REASON_LENGTH:
        raise InvalidChange(f"give a reason of at least {MIN_REASON_LENGTH} characters")
    return await _close(session, admin, request, "REJECTED", note)


async def cancel(session: AsyncSession, admin: AdminContext, request_id: uuid.UUID) -> ChangeRequest:
    request = await _pending(session, request_id)
    if request.requested_by != admin.user.id:
        raise AppError("NOT_REQUESTER", "only the admin who proposed this change can withdraw it", status_code=403)
    return await _close(session, admin, request, "CANCELLED", None)


async def _close(
    session: AsyncSession, admin: AdminContext, request: ChangeRequest, status: str, note: str | None
) -> ChangeRequest:
    request.status = status
    request.decided_by = admin.user.id if status == "REJECTED" else None
    request.decided_at = _now()
    request.decision_note = (note or "").strip() or None
    request.secret_values = {}
    await session.flush()
    await record(
        session,
        admin.actor,
        f"plugin.change_{status.lower()}",
        TARGET_PLUGIN,
        request.target_key,
        after={"request_id": str(request.id), "note": request.decision_note},
    )
    await session.commit()
    return request


# ---- Listing requests ------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class RequestView:
    request: ChangeRequest
    requested_by: AdminUser
    decided_by: AdminUser | None


async def list_requests(session: AsyncSession, status: str | None = None, limit: int = 50) -> list[RequestView]:
    query = select(ChangeRequest).order_by(ChangeRequest.created_at.desc()).limit(limit)
    if status:
        query = query.where(ChangeRequest.status == status)
    requests = (await session.scalars(query)).all()
    admin_ids = {r.requested_by for r in requests} | {r.decided_by for r in requests if r.decided_by}
    admins = (
        {a.id: a for a in (await session.scalars(select(AdminUser).where(AdminUser.id.in_(admin_ids)))).all()}
        if admin_ids
        else {}
    )
    return [
        RequestView(
            request=r,
            requested_by=admins[r.requested_by],
            decided_by=admins.get(r.decided_by) if r.decided_by else None,
        )
        for r in requests
    ]
