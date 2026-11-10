"""Runtime controls and feature switches: effective values and the maker-checker change flow (plan 3.2, M17).

- A control's effective value is its stored value, or its default from the config files when nothing is stored.
- A stored value the config files no longer allow (for example after a tightening pull request) falls back to the
  default, so a stale database row can never keep a control looser than the files permit.
- Loosening a control in production (enforce -> monitor, or a looser number) expires after a set time and raises a
  security alert. After expiry the previous value applies again.
- Proposing and approving use the shared change-request workflow in modules/plugins/service.py.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from paymentsapp.core.config import Environment
from paymentsapp.core.errors import AppError
from paymentsapp.modules.admin.service import AdminContext
from paymentsapp.modules.audit.service import record
from paymentsapp.modules.controls.catalogue import ControlCatalogue, ControlSpec, Value, get_control_catalogue
from paymentsapp.modules.controls.models import ControlSetting
from paymentsapp.modules.plugins.models import ChangeRequest
from paymentsapp.modules.plugins.service import MIN_REASON_LENGTH, InvalidChange, _now

log = logging.getLogger(__name__)

TARGET_CONTROL = "control"


@dataclass(frozen=True)
class ControlView:
    spec: ControlSpec
    value: Value  # effective value
    source: str  # "default", "set", "reverted" (a loosening expired) or "default (stored value not allowed)"
    setting: ControlSetting | None
    pending_request_id: uuid.UUID | None


def _effective(spec: ControlSpec, setting: ControlSetting | None, now: datetime) -> tuple[Value, str]:
    if setting is None:
        return spec.default, "default"
    value, source = setting.value, "set"
    if setting.expires_at is not None and setting.expires_at <= now:
        value, source = setting.revert_value, "reverted"
    if value is None or spec.validate(value) is not None:
        return spec.default, "default (stored value not allowed)"
    return value, source


async def list_controls(session: AsyncSession, catalogue: ControlCatalogue | None = None) -> list[ControlView]:
    catalogue = catalogue or get_control_catalogue()
    settings = {s.key: s for s in (await session.scalars(select(ControlSetting))).all()}
    pending = {
        r.target_key: r.id
        for r in (
            await session.scalars(
                select(ChangeRequest).where(
                    ChangeRequest.target_type == TARGET_CONTROL, ChangeRequest.status == "PENDING"
                )
            )
        ).all()
    }
    now = _now()
    views = []
    for key, spec in catalogue.controls.items():
        value, source = _effective(spec, settings.get(key), now)
        views.append(ControlView(spec, value, source, settings.get(key), pending.get(key)))
    return views


async def effective_values(session: AsyncSession, *, client_only: bool = False) -> dict[str, Value]:
    """Current value of every control, for enforcement in the backend or (client_only) for the mobile app."""
    return {v.spec.key: v.value for v in await list_controls(session) if v.spec.client or not client_only}


async def get_value(session: AsyncSession, key: str) -> Value:
    catalogue = get_control_catalogue()
    spec = catalogue.controls[key]
    value, _ = _effective(spec, await session.get(ControlSetting, key), _now())
    return value


# ---- Proposing -------------------------------------------------------------------------------------------------


async def propose_control_change(
    session: AsyncSession, admin: AdminContext, key: str, value: Any, reason: str
) -> ChangeRequest:
    catalogue = get_control_catalogue()
    spec = catalogue.controls.get(key)
    if spec is None:
        raise AppError("NOT_FOUND", "no such control", status_code=404)
    if len(reason.strip()) < MIN_REASON_LENGTH:
        raise InvalidChange(f"give a reason of at least {MIN_REASON_LENGTH} characters")
    problem = spec.validate(value)
    if problem:
        raise InvalidChange(f"{key} {problem}")

    setting = await session.get(ControlSetting, key, with_for_update=True)
    current, _ = _effective(spec, setting, _now())
    if value == current:
        raise InvalidChange("nothing would change")

    already_pending = await session.scalar(
        select(ChangeRequest.id).where(
            ChangeRequest.target_type == TARGET_CONTROL,
            ChangeRequest.target_key == key,
            ChangeRequest.status == "PENDING",
        )
    )
    if already_pending:
        raise _control_pending()

    request = ChangeRequest(
        id=uuid.uuid4(),
        target_type=TARGET_CONTROL,
        target_key=key,
        base_version=setting.version if setting else 0,
        changes={"value": value},
        secret_values={},
        secret_previews={},
        reason=reason.strip(),
        status="PENDING",
        requested_by=admin.user.id,
    )
    session.add(request)
    try:
        await session.flush()
    except IntegrityError:
        await session.rollback()
        raise _control_pending() from None
    loosening = catalogue.environment == Environment.PRODUCTION and spec.is_looser(current, value)
    await record(
        session,
        admin.actor,
        "control.change_requested",
        TARGET_CONTROL,
        key,
        before={"value": current},
        after={"request_id": str(request.id), "value": value, "reason": reason, "loosens_production": loosening},
    )
    await session.commit()
    return request


def _control_pending() -> AppError:
    return AppError("CHANGE_PENDING", "this control already has a change waiting for approval", status_code=409)


# ---- Applying (called by the shared approve step) ----------------------------------------------------------------


async def apply_control_change(
    session: AsyncSession, admin: AdminContext, request: ChangeRequest, now: datetime
) -> None:
    catalogue = get_control_catalogue()
    spec = catalogue.controls.get(request.target_key)
    if spec is None:
        raise InvalidChange("this control is no longer listed in the config files")
    setting = await session.get(ControlSetting, request.target_key, with_for_update=True)
    if (setting.version if setting else 0) != request.base_version:
        raise AppError("STALE_CHANGE", "the control changed after this request was made; reject it", status_code=409)

    new_value = request.changes["value"]
    problem = spec.validate(new_value)
    if problem:
        raise InvalidChange(f"{spec.key} {problem}")
    before, _ = _effective(spec, setting, now)

    loosening = catalogue.environment == Environment.PRODUCTION and spec.is_looser(before, new_value)
    expires_at = now + timedelta(hours=catalogue.loosening_expiry_hours) if loosening else None
    if setting is None:
        setting = ControlSetting(key=spec.key, version=0)
        session.add(setting)
    setting.value = new_value
    setting.expires_at = expires_at
    setting.revert_value = before if loosening else None
    setting.version += 1
    setting.updated_at = now
    setting.updated_by = str(admin.user.id)
    await session.flush()

    await record(
        session,
        admin.actor,
        "control.changed",
        TARGET_CONTROL,
        spec.key,
        before={"value": before},
        after={
            "value": new_value,
            "request_id": str(request.id),
            "requested_by": str(request.requested_by),
            "version": setting.version,
            "expires_at": expires_at.isoformat() if expires_at else None,
        },
    )
    if loosening:
        # Stands in for paging the security team until alerting exists (plan M15).
        log.warning(
            "SECURITY ALERT: production control loosened",
            extra={"control": spec.key, "from": before, "to": new_value, "expires_at": str(expires_at)},
        )
