"""Add durable background jobs and worker heartbeats.

Revision ID: 0005_durable_jobs
Revises: 0004_sku_catalog
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_durable_jobs"
down_revision: str | None = "0004_sku_catalog"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)
    document = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "jobs",
        sa.Column(
            "id",
            identifier,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("job_type", sa.String(64), nullable=False),
        sa.Column("idempotency_key", sa.String(200), nullable=False),
        sa.Column("payload", document, nullable=False),
        sa.Column("state", sa.String(16), server_default="queued", nullable=False),
        sa.Column("progress_current", sa.Integer(), server_default="0", nullable=False),
        sa.Column("progress_total", sa.Integer()),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("claimed_by", sa.String(128)),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True)),
        sa.Column(
            "cancellation_requested",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("result", document),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "job_type ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_jobs_type_format",
        ),
        sa.CheckConstraint(
            "btrim(idempotency_key) <> ''",
            name="ck_jobs_idempotency_key_not_blank",
        ),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
            name="ck_jobs_state",
        ),
        sa.CheckConstraint(
            "progress_current >= 0 AND "
            "(progress_total IS NULL OR "
            "(progress_total > 0 AND progress_current <= progress_total))",
            name="ck_jobs_progress",
        ),
        sa.CheckConstraint(
            "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20",
            name="ck_jobs_attempts",
        ),
        sa.CheckConstraint(
            "(state = 'running' AND claimed_by IS NOT NULL "
            "AND lease_expires_at IS NOT NULL) "
            "OR (state <> 'running' AND claimed_by IS NULL "
            "AND lease_expires_at IS NULL)",
            name="ck_jobs_claim",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "job_type",
            "idempotency_key",
            name="uq_jobs_type_idempotency",
        ),
    )
    op.create_index(
        "ix_jobs_claimable",
        "jobs",
        ["state", "available_at", "created_at"],
    )
    op.create_table(
        "job_attempts",
        sa.Column("job_id", identifier, nullable=False),
        sa.Column("attempt", sa.Integer(), nullable=False),
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), server_default="running", nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        sa.Column("error_code", sa.String(64)),
        sa.Column("error_summary", sa.String(500)),
        sa.CheckConstraint("attempt > 0", name="ck_job_attempts_attempt_positive"),
        sa.CheckConstraint(
            "state IN ('running', 'retry', 'succeeded', 'failed', "
            "'cancelled', 'abandoned')",
            name="ck_job_attempts_state",
        ),
        sa.ForeignKeyConstraint(["job_id"], ["jobs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("job_id", "attempt"),
    )
    op.create_table(
        "worker_heartbeats",
        sa.Column("worker_id", sa.String(128), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("current_job_id", identifier),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "heartbeat_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$'",
            name="ck_worker_heartbeats_id",
        ),
        sa.CheckConstraint(
            "state IN ('idle', 'running', 'stopping')",
            name="ck_worker_heartbeats_state",
        ),
        sa.ForeignKeyConstraint(
            ["current_job_id"],
            ["jobs.id"],
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("worker_id"),
    )
    op.create_index(
        "ix_worker_heartbeats_time",
        "worker_heartbeats",
        ["heartbeat_at"],
    )


def downgrade() -> None:
    op.drop_table("worker_heartbeats")
    op.drop_table("job_attempts")
    op.drop_table("jobs")
