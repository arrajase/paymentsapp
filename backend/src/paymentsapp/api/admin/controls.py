"""Admin API: runtime controls and feature switches (plan 3.2, M17). Approval uses /admin/v1/change-requests."""

from typing import Annotated

from fastapi import APIRouter, Depends

from paymentsapp.api.admin.plugins import one_request
from paymentsapp.api.deps import CurrentAdmin, DbSession, admin_ip_allowed, require_roles
from paymentsapp.modules.admin.service import AdminContext, AdminRole
from paymentsapp.modules.controls import service
from paymentsapp.modules.controls.schemas import ControlOut, ProposeControlChange
from paymentsapp.modules.plugins.schemas import ChangeRequestOut

router = APIRouter(prefix="/admin/v1", dependencies=[Depends(admin_ip_allowed)], tags=["admin"])

Maker = Annotated[AdminContext, Depends(require_roles(AdminRole.PLATFORM_ADMIN))]


def _control_out(view: service.ControlView) -> ControlOut:
    spec, setting = view.spec, view.setting
    return ControlOut(
        key=spec.key,
        kind=spec.kind,
        description=spec.description,
        value=view.value,
        source=view.source,
        default=spec.default,
        allowed=list(spec.allowed),
        bounds=list(spec.bounds) if spec.bounds else None,
        looser=spec.looser,
        client=spec.client,
        expires_at=setting.expires_at if setting else None,
        revert_value=setting.revert_value if setting else None,
        version=setting.version if setting else 0,
        updated_at=setting.updated_at if setting else None,
        updated_by=setting.updated_by if setting else None,
        pending_request_id=view.pending_request_id,
    )


@router.get("/controls", response_model=list[ControlOut])
async def list_controls(_: CurrentAdmin, session: DbSession) -> list[ControlOut]:
    return [_control_out(v) for v in await service.list_controls(session)]


@router.post("/controls/{key}/change-requests", response_model=ChangeRequestOut, status_code=201)
async def propose(key: str, body: ProposeControlChange, admin: Maker, session: DbSession) -> ChangeRequestOut:
    request = await service.propose_control_change(session, admin, key, body.value, body.reason)
    return await one_request(session, request.id)
