"""Add server-side sessions and authentication audit events.

Revision ID: 0011_authentication
Revises: 0010_model_registry
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011_authentication"
down_revision: str | None = "0010_model_registry"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)
    op.create_table(
        "auth_sessions",
        sa.Column(
            "id",
            identifier,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(length=64), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("token_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_auth_sessions_username_format",
        ),
        sa.CheckConstraint(
            "role IN ('owner', 'annotator', 'reviewer')",
            name="ck_auth_sessions_role",
        ),
        sa.CheckConstraint(
            "token_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_auth_sessions_token_sha256",
        ),
        sa.CheckConstraint("expires_at > created_at", name="ck_auth_sessions_expiry"),
        sa.UniqueConstraint("token_sha256", name="uq_auth_sessions_token_sha256"),
    )
    op.create_index(
        "ix_auth_sessions_active",
        "auth_sessions",
        ["token_sha256", "expires_at"],
    )
    op.create_table(
        "auth_events",
        sa.Column(
            "id",
            identifier,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("username", sa.String(length=64)),
        sa.Column("role", sa.String(length=16)),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("request_path", sa.String(length=512), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "username IS NULL OR username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
            name="ck_auth_events_username_format",
        ),
        sa.CheckConstraint(
            "role IS NULL OR role IN ('owner', 'annotator', 'reviewer')",
            name="ck_auth_events_role",
        ),
        sa.CheckConstraint(
            "action IN ('login_success', 'login_failure', 'logout', 'access_denied')",
            name="ck_auth_events_action",
        ),
        sa.CheckConstraint(
            "btrim(request_path) <> ''",
            name="ck_auth_events_path_not_blank",
        ),
    )
    op.create_index("ix_auth_events_created", "auth_events", ["created_at"])
    op.execute(
        """
        CREATE TRIGGER trg_auth_events_immutable
        BEFORE UPDATE OR DELETE
        ON auth_events
        FOR EACH ROW
        EXECUTE FUNCTION shelfsight_reject_snapshot_change();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS trg_auth_events_immutable ON auth_events")
    op.drop_index("ix_auth_events_created", table_name="auth_events")
    op.drop_table("auth_events")
    op.drop_index("ix_auth_sessions_active", table_name="auth_sessions")
    op.drop_table("auth_sessions")
