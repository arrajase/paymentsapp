"""Stored values of runtime controls and feature switches (plan 3.2).

A control with no row uses its default from the config files. A loosened production control carries expires_at
and the value to return to (revert_value); after expiry the effective value is revert_value again.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base


class ControlSetting(Base):
    __tablename__ = "control_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=False)  # "enforce", 24 or true
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revert_value: Mapped[Any | None] = mapped_column(JSONB)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
