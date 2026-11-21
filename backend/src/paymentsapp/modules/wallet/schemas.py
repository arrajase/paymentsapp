from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class WalletOut(BaseModel):
    balance: int  # paise
    currency: Literal["INR"]
    tier: Literal["T0", "T1", "T2"]
    enabled: bool  # the tier has a wallet
    max_balance: int | None
    can_add_now: int  # the most that can be added right now, in paise
    can_add_reason: str  # the limit that sets can_add_now
    merchant_payments: bool
    p2p_transfers: bool
    withdrawals: bool


class AddMoney(BaseModel):
    model_config = ConfigDict(extra="forbid")

    amount: int = Field(gt=0, le=10**10, description="paise")
    upi_link_id: UUID | None = Field(default=None, description="defaults to the default linked UPI ID")


class NewPayment(BaseModel):
    """Money out of the wallet. merchant needs payee_upi_id; p2p needs recipient_mobile; withdrawal goes to the
    customer's default linked UPI ID."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["merchant", "withdrawal", "p2p"]
    amount: int = Field(gt=0, le=10**10, description="paise")
    payee_upi_id: str | None = Field(default=None, max_length=100)
    recipient_mobile: str | None = Field(default=None, max_length=20)


class PayeeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    upi_id: str = Field(min_length=3, max_length=100)


class PayeeOut(BaseModel):
    upi_id: str
    name_masked: str
    active: bool


class RecipientIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mobile: str = Field(min_length=10, max_length=20)


class RecipientOut(BaseModel):
    mobile_masked: str
    name_masked: str
    can_receive: bool  # needs full KYC to receive wallet transfers


class PaymentOut(BaseModel):
    id: UUID
    kind: Literal["add_money", "merchant", "withdrawal", "p2p"]
    direction: Literal["in", "out"]  # as the customer looking at it sees it
    status: Literal["CREATED", "CHECKED", "AWAITING_AUTH", "UNKNOWN", "SUCCESS", "FAILED"]
    amount: int  # paise
    currency: Literal["INR"]
    counterparty: str  # the other side, masked: a UPI ID or a mobile number
    failure_reason: str | None
    failure_message: str | None  # safe to show the customer
    created_at: datetime
    completed_at: datetime | None
    expires_at: datetime | None  # when the bank stops waiting for approval


class AddMoneyOut(PaymentOut):
    approve_at: str | None  # the bank website, where the customer approves with their UPI PIN
