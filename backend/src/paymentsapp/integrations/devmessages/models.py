"""Development only: messages the dev_messages plugin "sent", shown in the admin website's Dev inbox.

They hold real OTPs for test customers, so the table is used only when ENVIRONMENT=development, and entries older
than a day are deleted whenever the inbox is read.
"""

from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Identity, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base


class DevMessage(Base):
    __tablename__ = "dev_messages"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    channel: Mapped[str] = mapped_column(String(8), nullable=False)  # "sms" or "email"
    to_address: Mapped[str] = mapped_column(String(254), nullable=False)
    purpose: Mapped[str] = mapped_column(String(32), nullable=False)
    subject: Mapped[str | None] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, nullable=False)
    pushed_to_emulator: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
