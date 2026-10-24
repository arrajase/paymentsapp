"""admin login id instead of email

Admins sign in with a login ID (for example "platform_admin"); an email address is still a valid login ID.

Revision ID: 19d4bb247445
Revises: d78359c8d9ce
Create Date: 2026-10-24 14:24:00

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "19d4bb247445"
down_revision: str | Sequence[str] | None = "d78359c8d9ce"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("admin_users", "email", new_column_name="login_id", type_=sa.String(length=64))
    op.execute("ALTER TABLE admin_users RENAME CONSTRAINT uq_admin_users_email TO uq_admin_users_login_id")


def downgrade() -> None:
    op.execute("ALTER TABLE admin_users RENAME CONSTRAINT uq_admin_users_login_id TO uq_admin_users_email")
    op.alter_column("admin_users", "login_id", new_column_name="email", type_=sa.String(length=254))
