"""Consent records (plan M9, DPDP): one row per grant, with the notice version accepted. Withdrawal is recorded on
the row rather than deleting it, so there is evidence of what was agreed and when.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, String, Uuid, text
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base


class ConsentRecord(Base):
    __tablename__ = "consent_records"
    __table_args__ = (
        # At most one active consent per purpose per customer.
        Index(
            "uq_consent_records_active",
            "customer_id",
            "purpose",
            unique=True,
            postgresql_where=text("withdrawn_at IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    customer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("customers.id"), nullable=False, index=True)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    notice_version: Mapped[str] = mapped_column(String(32), nullable=False)
    granted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    withdrawn_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
