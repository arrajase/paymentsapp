from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class FieldOut(BaseModel):
    name: str
    label: str
    kind: str
    value: str | None


class SecretOut(BaseModel):
    name: str
    label: str
    preview: str | None  # e.g. "mbk_…x9k2"; the value itself is never returned
    set_at: datetime | None
    set_by: str | None


class PluginOut(BaseModel):
    key: str
    description: str
    interfaces: list[str]
    mode: str
    allowed_modes: list[str]
    enabled: bool
    version: int
    updated_at: datetime
    updated_by: str
    config: list[FieldOut]
    secrets: list[SecretOut]
    pending_request_id: UUID | None


class ProposeChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=500)
    enabled: bool | None = None
    mode: str | None = Field(default=None, max_length=16)
    config: dict[str, str] | None = None
    secrets: dict[str, str] | None = None  # write-only


class Decision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    note: str | None = Field(default=None, max_length=500)


class AdminRef(BaseModel):
    id: UUID
    login_id: str
    display_name: str


class ChangeRequestOut(BaseModel):
    id: UUID
    target_type: str
    target_key: str
    status: Literal["PENDING", "APPROVED", "REJECTED", "CANCELLED"]
    changes: dict[str, Any]
    secrets: dict[str, str]  # name -> preview
    reason: str
    requested_by: AdminRef
    requested_at: datetime
    decided_by: AdminRef | None
    decided_at: datetime | None
    decision_note: str | None


class AuditEntryOut(BaseModel):
    id: int
    occurred_at: datetime
    actor_type: str
    actor_id: str | None
    action: str
    resource_type: str
    resource_id: str | None
    ip: str | None
    before: dict[str, Any] | None
    after: dict[str, Any] | None


class SystemOut(BaseModel):
    environment: str
    webhook_url: str  # what to register at the mock bank with its set-webhook command
