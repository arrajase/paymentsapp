from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

ControlValue = bool | int | str  # bool first, so JSON true/false stay booleans


class ControlOut(BaseModel):
    key: str
    kind: str  # "state", "number" or "feature"
    description: str
    value: ControlValue  # effective value right now
    source: str
    default: ControlValue
    allowed: list[ControlValue]  # state and feature controls
    bounds: list[int] | None  # number controls
    looser: str | None
    client: bool
    expires_at: datetime | None  # set when a production control was loosened
    revert_value: ControlValue | None
    version: int
    updated_at: datetime | None
    updated_by: str | None
    pending_request_id: UUID | None


class ProposeControlChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: ControlValue
    reason: str = Field(min_length=1, max_length=500)


class AppConfigOut(BaseModel):
    """What the mobile app needs to adjust its own behaviour. The server enforces every control regardless."""

    environment: str
    controls: dict[str, ControlValue]
    generated_at: datetime
