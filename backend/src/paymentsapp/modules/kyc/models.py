"""KYC profile and tier (plan M2, slim version for Phase 1).

T0: registered. T1: verified mobile plus self-declared name and date of birth (18 or older). T2: full KYC, which
in development comes from the mock_kyc plugin. Document checks (PAN, Aadhaar, CKYC) arrive with a real KYC vendor.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, Date, DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base, CreatedAtMixin


class KycProfile(CreatedAtMixin, Base):
    __tablename__ = "kyc_profiles"
    __table_args__ = (CheckConstraint("tier IN ('T0', 'T1', 'T2')", name="tier"),)

    customer_id: Mapped[uuid.UUID] = mapped_column(Uuid, ForeignKey("customers.id"), primary_key=True)
    tier: Mapped[str] = mapped_column(String(2), nullable=False, default="T0")
    full_name: Mapped[str | None] = mapped_column(String(100))
    date_of_birth: Mapped[date | None] = mapped_column(Date)
    minimum_kyc_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_kyc_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    full_kyc_provider: Mapped[str | None] = mapped_column(String(32))
    full_kyc_reference: Mapped[str | None] = mapped_column(String(64))
