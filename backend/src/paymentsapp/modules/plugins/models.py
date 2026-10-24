"""Plugin settings, their encrypted secrets, and change requests (plan 3.1, M17).

A plugin changes only when a change request proposed by one admin is approved by another. Change requests are
generic (target_type) so runtime controls (plan 3.2) can use the same workflow.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Index, Integer, String, Text, Uuid, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from paymentsapp.db.base import Base, CreatedAtMixin


class PluginSetting(Base):
    __tablename__ = "plugin_settings"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # plugin key from providers.yaml
    mode: Mapped[str] = mapped_column(String(16), nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False)
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)  # admin ID, or "system" for seeding


class PluginSecret(Base):
    __tablename__ = "plugin_secrets"

    plugin_key: Mapped[str] = mapped_column(ForeignKey("plugin_settings.key"), primary_key=True)
    name: Mapped[str] = mapped_column(String(64), primary_key=True)
    value_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    preview: Mapped[str] = mapped_column(String(32), nullable=False)  # e.g. "mbk_…x9k2", safe to display
    set_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    set_by: Mapped[str] = mapped_column(String(64), nullable=False)


class ChangeRequest(CreatedAtMixin, Base):
    __tablename__ = "change_requests"
    __table_args__ = (
        CheckConstraint("status IN ('PENDING', 'APPROVED', 'REJECTED', 'CANCELLED')", name="status"),
        CheckConstraint("decided_by IS NULL OR decided_by <> requested_by", name="two_people"),
        # At most one open request per target, so approvals never race each other.
        Index(
            "uq_change_requests_one_pending",
            "target_type",
            "target_key",
            unique=True,
            postgresql_where=text("status = 'PENDING'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    target_type: Mapped[str] = mapped_column(String(16), nullable=False)  # "plugin" (later also "control")
    target_key: Mapped[str] = mapped_column(String(64), nullable=False)
    base_version: Mapped[int] = mapped_column(Integer, nullable=False)  # target's version when proposed
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)  # non-secret: enabled, mode, config
    # Proposed secret values, encrypted; cleared once the request is decided so no copy outlives it.
    secret_values: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    secret_previews: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    reason: Mapped[str] = mapped_column(String(500), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="PENDING")
    requested_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("admin_users.id"), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("admin_users.id"))
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    decision_note: Mapped[str | None] = mapped_column(String(500))
