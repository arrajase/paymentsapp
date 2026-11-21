"""Bank events already received, and how far the poller has read (plan 9.4, 9.5).

Banks deliver at least once, and the same event can also arrive by webhook and by polling, so every event is
claimed once by its ID. Only the event's ID and type are kept, not its contents, which can include customers' UPI
IDs and names.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, Text, func
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
    source: Mapped[str] = mapped_column(String(8), nullable=False, server_default="webhook")  # or "poll"


class BankEventCursor(Base):
    """The poller's position in a bank's event feed: the last event it handled, and how its last run went."""

    __tablename__ = "bank_event_cursors"

    provider: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_event_id: Mapped[str | None] = mapped_column(String(64))
    last_polled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    events_handled: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
