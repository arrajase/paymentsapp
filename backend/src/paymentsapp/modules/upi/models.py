"""Linked UPI IDs and the attempts that count towards the UPI check limit (plan M3).

The UPI ID is stored encrypted with a blind index for lookup, like customer identifiers. The account-holder name
is kept only masked; the full name is used once, for the name match, and not stored.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, CheckConstraint, DateTime, Float, ForeignKey, Index, String, Text, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base, CreatedAtMixin


class UpiLink(CreatedAtMixin, Base):
    """pending: waiting for the customer to approve the LINK consent at the bank. active: linked.
    failed: the consent was rejected, expired or never approved. removed: unlinked by the customer, or the bank
    consent was revoked or expired.
    """

    __tablename__ = "upi_links"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'active', 'failed', 'removed')", name="status"),
        CheckConstraint("NOT is_default OR status = 'active'", name="default_is_active"),
        # A customer links a UPI ID once, and a UPI ID is active for one customer only.
        Index(
            "uq_upi_links_customer_upi_open",
            "customer_id",
            "blind_index",
            unique=True,
            postgresql_where=text("status IN ('pending', 'active')"),
        ),
        Index("uq_upi_links_upi_active", "blind_index", unique=True, postgresql_where=text("status = 'active'")),
        Index("uq_upi_links_one_default", "customer_id", unique=True, postgresql_where=text("is_default")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)  # plugin key, e.g. "mockbank"
    upi_id_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    blind_index: Mapped[str] = mapped_column(String(64), nullable=False)
    upi_id_masked: Mapped[str] = mapped_column(String(80), nullable=False)
    holder_name_masked: Mapped[str] = mapped_column(String(120), nullable=False)
    name_match_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Whether the bank links this UPI ID to the verified mobile; None when not checked (sim_binding off).
    found_by_mobile: Mapped[bool | None] = mapped_column(Boolean)
    consent_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    consent_expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="pending")
    status_reason: Mapped[str | None] = mapped_column(String(32))
    is_default: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UpiCheck(CreatedAtMixin, Base):
    """One call to the bank's verify or lookup for a customer, counted by the vpa_verifications_per_hour control."""

    __tablename__ = "upi_checks"
    __table_args__ = (Index("ix_upi_checks_customer_id_created_at", "customer_id", "created_at"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)  # "verify" or "lookup"
