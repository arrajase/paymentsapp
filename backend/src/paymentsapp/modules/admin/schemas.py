from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SignInRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    login_id: str = Field(min_length=3, max_length=64)
    password: str = Field(min_length=1, max_length=256)
    totp_code: str | None = Field(default=None, pattern=r"^\d{6}$")


class SignInOptions(BaseModel):
    totp_required: bool


class AdminOut(BaseModel):
    id: UUID
    login_id: str
    display_name: str
    roles: list[str]


class SignInResponse(BaseModel):
    token: str
    expires_at: datetime
    idle_timeout_minutes: int
    admin: AdminOut
