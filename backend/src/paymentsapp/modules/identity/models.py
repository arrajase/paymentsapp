"""Customers, their verified identifiers, OTP challenges, devices, sessions and tokens (plan M1).

Identifiers (email, mobile) are stored encrypted, with a blind index for lookup, so the database never holds them in
plain text. OTPs, PINs and tokens are stored only as hashes.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base, CreatedAtMixin


class Customer(CreatedAtMixin, Base):
    __tablename__ = "customers"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'locked', 'closed')", name="status"),
        CheckConstraint("pin_failed_attempts >= 0", name="pin_failed_attempts"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    pin_hash: Mapped[str | None] = mapped_column(String(255))
    pin_failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    pin_locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerIdentifier(CreatedAtMixin, Base):
    __tablename__ = "customer_identifiers"
    __table_args__ = (
        CheckConstraint("kind IN ('email', 'mobile')", name="kind"),
        UniqueConstraint("kind", "blind_index", name="uq_customer_identifiers_kind_blind_index"),
        UniqueConstraint("customer_id", "kind", name="uq_customer_identifiers_customer_kind"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    blind_index: Mapped[str] = mapped_column(String(64), nullable=False)
    masked: Mapped[str] = mapped_column(String(64), nullable=False)  # e.g. "+91 90000 •••01"
    verified_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class OtpChallenge(CreatedAtMixin, Base):
    __tablename__ = "otp_challenges"
    __table_args__ = (
        CheckConstraint("kind IN ('email', 'mobile')", name="kind"),
        CheckConstraint("purpose IN ('sign_in', 'add_identifier')", name="purpose"),
        Index("ix_otp_challenges_blind_index_created_at", "blind_index", "created_at"),
        Index("ix_otp_challenges_request_ip_created_at", "request_ip", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    blind_index: Mapped[str] = mapped_column(String(64), nullable=False)
    # The identifier itself, encrypted: needed to create the identifier row once the code is verified.
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    purpose: Mapped[str] = mapped_column(String(16), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))  # set for add_identifier
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    request_ip: Mapped[str | None] = mapped_column(String(45))


class EnrolmentToken(CreatedAtMixin, Base):
    """Proof that an OTP was verified, exchanged once for a device registration within a few minutes."""

    __tablename__ = "enrolment_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    challenge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("otp_challenges.id"), nullable=False)
    customer_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("customers.id"))  # None: a new customer
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Device(CreatedAtMixin, Base):
    __tablename__ = "devices"
    __table_args__ = (
        CheckConstraint("status IN ('active', 'revoked')", name="status"),
        # One active device per customer, as UPI apps do (plan M1).
        Index("uq_devices_one_active", "customer_id", unique=True, postgresql_where=text("status = 'active'")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    public_key: Mapped[str] = mapped_column(Text, nullable=False)  # base64 DER SubjectPublicKeyInfo, ECDSA P-256
    hardware_backed: Mapped[bool] = mapped_column(Boolean, nullable=False)  # as claimed by the app
    platform: Mapped[str] = mapped_column(String(16), nullable=False)
    label: Mapped[str | None] = mapped_column(String(100))
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    cooling_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class DeviceChallenge(CreatedAtMixin, Base):
    __tablename__ = "device_challenges"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    nonce: Mapped[str] = mapped_column(String(64), nullable=False)
    # None for a sign-in challenge; the sensitive action for a step-up challenge (for example "upi_link").
    action: Mapped[str | None] = mapped_column(String(32))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CustomerSession(CreatedAtMixin, Base):
    """One signed-in session on one device. Its refresh tokens form a family: reuse of an old one revokes it."""

    __tablename__ = "customer_sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    device_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("devices.id"), nullable=False)
    last_used_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoke_reason: Mapped[str | None] = mapped_column(String(32))


class CustomerToken(CreatedAtMixin, Base):
    __tablename__ = "customer_tokens"
    __table_args__ = (CheckConstraint("kind IN ('access', 'refresh')", name="kind"),)

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_sessions.id"), nullable=False, index=True)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # refresh tokens: when rotated


class StepUpToken(CreatedAtMixin, Base):
    """Proof of a fresh PIN or biometric plus device signature, for one sensitive action, usable once (plan M1)."""

    __tablename__ = "step_up_tokens"

    token_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customer_sessions.id"), nullable=False, index=True)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
