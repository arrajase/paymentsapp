from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class UpiIdIn(_In):
    upi_id: str = Field(min_length=3, max_length=100)


class CandidateOut(BaseModel):
    upi_id: str
    holder_name_masked: str  # e.g. "T••• U•••"; the full name is never sent
    name_matches: bool
    found_by_mobile: bool  # the bank links this UPI ID to the customer's verified mobile number
    can_link: bool
    problem_code: str | None
    problem: str | None


class DiscoverOut(BaseModel):
    found: bool
    candidate: CandidateOut | None


class LinkOut(BaseModel):
    id: UUID
    upi_id: str
    holder_name_masked: str
    status: Literal["pending", "active", "failed", "removed"]
    status_reason: str | None
    is_default: bool
    created_at: datetime
    activated_at: datetime | None
    consent_expires_at: datetime


class LinkCreatedOut(LinkOut):
    approve_at: str | None  # the bank website, where the customer approves the request with their UPI PIN


class LinkUpdate(_In):
    is_default: Literal[True]
