"""Bank events (webhooks, and later the /events poller) already received, so each is handled once (plan 9.4).

Banks deliver at least once, so the same event can arrive several times. Only the event's ID and type are kept,
not its contents, which can include customers' UPI IDs and names.
"""

from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base


class BankEventReceipt(Base):
    __tablename__ = "bank_events"

    event_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    type: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # "processed" or "ignored"
