"""Admin API: plugins, change requests, audit log and system info (plan M16, M17)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from paymentsapp.api.deps import CurrentAdmin, DbSession, admin_ip_allowed, require_roles
from paymentsapp.core.config import get_settings
from paymentsapp.modules.admin.models import AdminUser
from paymentsapp.modules.admin.service import AdminContext, AdminRole
from paymentsapp.modules.audit.models import AuditEntry
from paymentsapp.modules.plugins import health, service
from paymentsapp.modules.plugins.models import PluginHealthCheck
from paymentsapp.modules.plugins.schemas import (
    AdminRef,
    AuditEntryOut,
    ChangeRequestOut,
    Decision,
    EventFeedOut,
    FieldOut,
    HealthCheckOut,
    PluginHealthOut,
    PluginOut,
    ProposeChange,
    SecretOut,
    SystemOut,
)

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(admin_ip_allowed)], tags=["admin"])

Maker = Annotated[AdminContext, Depends(require_roles(AdminRole.PLATFORM_ADMIN))]
Checker = Annotated[AdminContext, Depends(require_roles(AdminRole.SECURITY_APPROVER))]


def _plugin_out(view: service.PluginView) -> PluginOut:
    setting = view.setting
    return PluginOut(
        key=setting.key,
        description=view.spec.description,
        interfaces=list(view.interfaces),
        mode=setting.mode,
        allowed_modes=list(view.allowed_modes),
        enabled=setting.enabled,
        version=setting.version,
        updated_at=setting.updated_at,
        updated_by=setting.updated_by,
        config=[
            FieldOut(name=f.name, label=f.label, kind=f.kind, value=setting.config.get(f.name))
            for f in view.spec.config
        ],
        secrets=[
            SecretOut(name=s.name, label=s.label, preview=s.preview, set_at=s.set_at, set_by=s.set_by)
            for s in view.secrets
        ],
        pending_request_id=view.pending_request_id,
    )


def _ref(user: AdminUser) -> AdminRef:
    return AdminRef(id=user.id, login_id=user.login_id, display_name=user.display_name)


def _request_out(view: service.RequestView) -> ChangeRequestOut:
    r = view.request
    return ChangeRequestOut(
        id=r.id,
        target_type=r.target_type,
        target_key=r.target_key,
        status=r.status,
        changes=r.changes,
        secrets=r.secret_previews,
        reason=r.reason,
        requested_by=_ref(view.requested_by),
        requested_at=r.created_at,
        decided_by=_ref(view.decided_by) if view.decided_by else None,
        decided_at=r.decided_at,
        decision_note=r.decision_note,
    )


async def one_request(session: DbSession, request_id: uuid.UUID) -> ChangeRequestOut:
    views = {v.request.id: v for v in await service.list_requests(session, limit=500)}
    return _request_out(views[request_id])


@router.get("/system", response_model=SystemOut)
async def system(_: CurrentAdmin) -> SystemOut:
    settings = get_settings()
    return SystemOut(
        environment=settings.environment.value,
        webhook_url=f"{settings.public_base_url.rstrip('/')}/v1/webhooks/mockbank",
    )


@router.get("/plugins", response_model=list[PluginOut])
async def list_plugins(_: CurrentAdmin, session: DbSession) -> list[PluginOut]:
    return [_plugin_out(v) for v in await service.list_plugins(session)]


def _check_out(check: PluginHealthCheck | None) -> HealthCheckOut | None:
    if check is None:
        return None
    return HealthCheckOut(checked_at=check.checked_at, ok=check.ok, latency_ms=check.latency_ms, detail=check.detail)


@router.get("/plugins/health", response_model=list[PluginHealthOut])
async def plugin_health(_: CurrentAdmin, session: DbSession) -> list[PluginHealthOut]:
    return [
        PluginHealthOut(
            key=h.setting.key,
            enabled=h.setting.enabled,
            mode=h.setting.mode,
            remote=h.remote,
            circuit=h.circuit,
            last_check=_check_out(h.last_check),
            events=EventFeedOut(**h.events.__dict__) if h.events else None,
        )
        for h in await health.overview(session)
    ]


@router.post("/plugins/{plugin_key}/health-check", response_model=HealthCheckOut)
async def run_health_check(plugin_key: str, admin: CurrentAdmin, session: DbSession) -> HealthCheckOut:
    """Reach the partner now. Can take up to a minute while a sleeping mock bank wakes up."""
    result = _check_out(await health.check(session, admin, plugin_key))
    assert result is not None
    return result


@router.post("/plugins/{plugin_key}/change-requests", response_model=ChangeRequestOut, status_code=201)
async def propose(plugin_key: str, body: ProposeChange, admin: Maker, session: DbSession) -> ChangeRequestOut:
    request = await service.propose_change(
        session,
        admin,
        plugin_key,
        reason=body.reason,
        enabled=body.enabled,
        mode=body.mode,
        config=body.config,
        secrets=body.secrets,
    )
    return await one_request(session, request.id)


@router.get("/change-requests", response_model=list[ChangeRequestOut])
async def list_change_requests(
    _: CurrentAdmin,
    session: DbSession,
    status: Annotated[Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"] | None, Query()] = None,
) -> list[ChangeRequestOut]:
    return [_request_out(v) for v in await service.list_requests(session, status)]


@router.post("/change-requests/{request_id}/approve", response_model=ChangeRequestOut)
async def approve(request_id: uuid.UUID, body: Decision, admin: Checker, session: DbSession) -> ChangeRequestOut:
    await service.approve(session, admin, request_id, body.note)
    return await one_request(session, request_id)


@router.post("/change-requests/{request_id}/reject", response_model=ChangeRequestOut)
async def reject(request_id: uuid.UUID, body: Decision, admin: Checker, session: DbSession) -> ChangeRequestOut:
    await service.reject(session, admin, request_id, body.note or "")
    return await one_request(session, request_id)


@router.post("/change-requests/{request_id}/cancel", response_model=ChangeRequestOut)
async def cancel(request_id: uuid.UUID, admin: Maker, session: DbSession) -> ChangeRequestOut:
    await service.cancel(session, admin, request_id)
    return await one_request(session, request_id)


@router.get("/audit", response_model=list[AuditEntryOut])
async def audit(
    _: CurrentAdmin,
    session: DbSession,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
    before_id: Annotated[int | None, Query(ge=1)] = None,
) -> list[AuditEntryOut]:
    query = select(AuditEntry).order_by(AuditEntry.id.desc()).limit(limit)
    if before_id:
        query = query.where(AuditEntry.id < before_id)
    return [
        AuditEntryOut(
            id=e.id,
            occurred_at=e.occurred_at,
            actor_type=e.actor_type,
            actor_id=e.actor_id,
            action=e.action,
            resource_type=e.resource_type,
            resource_id=e.resource_id,
            ip=e.ip,
            before=e.before,
            after=e.after,
        )
        for e in (await session.scalars(query)).all()
    ]
