"""Add evaluated model registry and deployment history.

Revision ID: 0010_model_registry
Revises: 0009_review_quality_assurance
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010_model_registry"
down_revision: str | None = "0009_review_quality_assurance"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

identifier = postgresql.UUID(as_uuid=True)
json_document = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.create_table(
        "model_registry_entries",
        sa.Column(
            "id",
            identifier,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("model_role", sa.String(length=64), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("model_version", sa.String(length=128), nullable=False),
        sa.Column(
            "model_artifact_id",
            identifier,
            sa.ForeignKey("snapshot_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_artifact_id",
            identifier,
            sa.ForeignKey("snapshot_artifacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "training_dataset_version_id",
            identifier,
            sa.ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "evaluation_dataset_version_id",
            identifier,
            sa.ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("evaluation_artifact_sha256", sa.String(length=64), nullable=False),
        sa.Column("configuration", json_document, nullable=False),
        sa.Column("compatibility", json_document, nullable=False),
        sa.Column("metrics", json_document, nullable=False),
        sa.Column("registered_by", sa.String(length=128), nullable=False),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "model_role ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_model_registry_role_format",
        ),
        sa.CheckConstraint(
            "btrim(model_id) <> '' AND btrim(model_version) <> ''",
            name="ck_model_registry_identity_not_blank",
        ),
        sa.CheckConstraint(
            "model_artifact_sha256 ~ '^[0-9a-f]{64}$' "
            "AND evaluation_artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_model_registry_artifact_sha256",
        ),
        sa.CheckConstraint(
            "btrim(registered_by) <> ''",
            name="ck_model_registry_actor_not_blank",
        ),
        sa.UniqueConstraint(
            "model_role",
            "model_id",
            "model_version",
            name="uq_model_registry_identity",
        ),
        sa.UniqueConstraint(
            "model_role",
            "model_artifact_sha256",
            name="uq_model_registry_role_artifact",
        ),
    )
    op.create_index(
        "ix_model_registry_role_registered",
        "model_registry_entries",
        ["model_role", "registered_at"],
    )
    op.create_table(
        "model_deployments",
        sa.Column("model_role", sa.String(length=64), primary_key=True),
        sa.Column(
            "active_entry_id",
            identifier,
            sa.ForeignKey("model_registry_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "previous_entry_id",
            identifier,
            sa.ForeignKey("model_registry_entries.id", ondelete="SET NULL"),
        ),
        sa.Column("updated_by", sa.String(length=128), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "model_role ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_model_deployments_role_format",
        ),
        sa.CheckConstraint(
            "previous_entry_id IS NULL OR previous_entry_id <> active_entry_id",
            name="ck_model_deployments_distinct_entries",
        ),
        sa.CheckConstraint(
            "btrim(updated_by) <> ''",
            name="ck_model_deployments_actor_not_blank",
        ),
    )
    op.create_table(
        "model_deployment_events",
        sa.Column(
            "id",
            identifier,
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("model_role", sa.String(length=64), nullable=False),
        sa.Column(
            "from_entry_id",
            identifier,
            sa.ForeignKey("model_registry_entries.id", ondelete="CASCADE"),
        ),
        sa.Column(
            "to_entry_id",
            identifier,
            sa.ForeignKey("model_registry_entries.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("action", sa.String(length=16), nullable=False),
        sa.Column("actor", sa.String(length=128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.CheckConstraint(
            "model_role ~ '^[a-z][a-z0-9_]{0,63}$'",
            name="ck_model_deployment_events_role_format",
        ),
        sa.CheckConstraint(
            "action IN ('promote', 'rollback')",
            name="ck_model_deployment_events_action",
        ),
        sa.CheckConstraint(
            "from_entry_id IS NULL OR from_entry_id <> to_entry_id",
            name="ck_model_deployment_events_distinct_entries",
        ),
        sa.CheckConstraint(
            "btrim(actor) <> ''",
            name="ck_model_deployment_events_actor_not_blank",
        ),
    )
    op.create_index(
        "ix_model_deployment_events_role_created",
        "model_deployment_events",
        ["model_role", "created_at"],
    )
    for table_name in ("model_registry_entries", "model_deployment_events"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE
            ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION shelfsight_reject_snapshot_change();
            """
        )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS trg_model_deployment_events_immutable "
        "ON model_deployment_events"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_model_registry_entries_immutable "
        "ON model_registry_entries"
    )
    op.drop_index(
        "ix_model_deployment_events_role_created",
        table_name="model_deployment_events",
    )
    op.drop_table("model_deployment_events")
    op.drop_table("model_deployments")
    op.drop_index(
        "ix_model_registry_role_registered",
        table_name="model_registry_entries",
    )
    op.drop_table("model_registry_entries")
