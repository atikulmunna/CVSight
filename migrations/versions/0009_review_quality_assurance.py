"""Add review decisions and dataset snapshot sign-off.

Revision ID: 0009_review_quality_assurance
Revises: 0008_immutable_snapshot_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_review_quality_assurance"
down_revision: str | None = "0008_immutable_snapshot_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_decisions",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_versions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("annotation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("annotation_revision", sa.Integer(), nullable=False),
        sa.Column("decision", sa.String(length=16), nullable=False),
        sa.Column(
            "risk_reasons",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column("reviewer", sa.String(length=128), nullable=False),
        sa.Column("note", sa.String(length=500)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["annotation_id", "annotation_revision"],
            ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
            name="fk_review_decisions_annotation_revision",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "annotation_revision > 0",
            name="ck_review_decisions_revision_positive",
        ),
        sa.CheckConstraint(
            "decision IN ('approved', 'flagged')",
            name="ck_review_decisions_decision",
        ),
        sa.CheckConstraint(
            "btrim(reviewer) <> ''",
            name="ck_review_decisions_reviewer_not_blank",
        ),
        sa.CheckConstraint(
            "note IS NULL OR btrim(note) <> ''",
            name="ck_review_decisions_note_not_blank",
        ),
    )
    op.create_index(
        "ix_review_decisions_version_annotation",
        "review_decisions",
        ["dataset_version_id", "annotation_id", "annotation_revision", "created_at"],
    )
    op.create_table(
        "dataset_review_signoffs",
        sa.Column(
            "dataset_version_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("signed_by", sa.String(length=128), nullable=False),
        sa.Column(
            "signed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("reviewed_annotation_count", sa.Integer(), nullable=False),
        sa.Column("risk_item_count", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "btrim(signed_by) <> ''",
            name="ck_dataset_review_signoffs_actor_not_blank",
        ),
        sa.CheckConstraint(
            "reviewed_annotation_count >= 0 AND risk_item_count >= 0",
            name="ck_dataset_review_signoffs_counts",
        ),
    )
    for table_name in ("review_decisions", "dataset_review_signoffs"):
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
        "DROP TRIGGER IF EXISTS trg_dataset_review_signoffs_immutable "
        "ON dataset_review_signoffs"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS trg_review_decisions_immutable ON review_decisions"
    )
    op.drop_table("dataset_review_signoffs")
    op.drop_index(
        "ix_review_decisions_version_annotation",
        table_name="review_decisions",
    )
    op.drop_table("review_decisions")
