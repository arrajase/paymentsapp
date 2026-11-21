from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

Kind = Literal["email", "mobile"]


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OtpRequest(_In):
    kind: Kind
    identifier: str = Field(min_length=3, max_length=254)


class OtpRequested(BaseModel):
    challenge_id: UUID
    sent_to: str  # masked
    expires_at: datetime
    resend_after_seconds: int


class OtpVerify(_In):
    challenge_id: UUID
    code: str = Field(pattern=r"^\d{6}$")


class NoticeOut(BaseModel):
    purpose: str
    version: str
    title: str
    summary: str
    required: bool


class CodeVerifiedOut(BaseModel):
    enrolment_token: str
    expires_at: datetime
    next_step: Literal["create_account", "set_pin", "enter_pin"]
    terms: NoticeOut | None  # shown when next_step is create_account


class DeviceEnrol(_In):
    enrolment_token: str = Field(min_length=10, max_length=128)
    public_key: str = Field(min_length=40, max_length=512, description="base64 DER SubjectPublicKeyInfo, ECDSA P-256")
    platform: Literal["android", "ios"]
    hardware_backed: bool
    label: str | None = Field(default=None, max_length=100)
    pin: str = Field(pattern=r"^\d{4,6}$")
    accepted_terms_version: str | None = Field(default=None, max_length=32)


class TokensOut(BaseModel):
    access_token: str
    access_expires_at: datetime
    refresh_token: str
    refresh_expires_at: datetime


class EnrolledOut(BaseModel):
    customer_id: UUID
    device_id: UUID
    new_customer: bool
    replaced_device: bool
    tokens: TokensOut


class ChallengeRequest(_In):
    device_id: UUID


class ChallengeOut(BaseModel):
    challenge_id: UUID
    nonce: str
    expires_at: datetime
    sign: str  # the exact text to sign, for clarity


class DeviceSignIn(_In):
    device_id: UUID
    challenge_id: UUID
    signature: str = Field(min_length=40, max_length=200, description="base64 DER ECDSA signature (SHA-256)")
    pin: str | None = Field(default=None, pattern=r"^\d{4,6}$")
    biometric: bool = False


class StepUpChallengeRequest(_In):
    action: Literal["upi_link", "upi_unlink", "upi_set_default", "payment"]


class StepUpRequest(_In):
    challenge_id: UUID
    signature: str = Field(min_length=40, max_length=200, description="base64 DER ECDSA signature (SHA-256)")
    pin: str | None = Field(default=None, pattern=r"^\d{4,6}$")
    biometric: bool = False


class StepUpOut(BaseModel):
    step_up_token: str
    action: str
    expires_at: datetime


class RefreshRequest(_In):
    refresh_token: str = Field(min_length=10, max_length=128)
    device_id: UUID


class IdentifierOut(BaseModel):
    kind: Kind
    masked: str
    verified_at: datetime


class DeviceOut(BaseModel):
    id: UUID
    platform: str
    label: str | None
    hardware_backed: bool
    cooling_until: datetime | None


class KycOut(BaseModel):
    tier: Literal["T0", "T1", "T2"]
    full_name: str | None
    date_of_birth: date | None


class MeOut(BaseModel):
    customer_id: UUID
    identifiers: list[IdentifierOut]
    device: DeviceOut
    kyc: KycOut
    pin_set: bool


class AddIdentifierVerify(_In):
    challenge_id: UUID
    code: str = Field(pattern=r"^\d{6}$")
