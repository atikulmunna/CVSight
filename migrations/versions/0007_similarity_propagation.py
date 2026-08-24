"""Add auditable similarity propagation.

Revision ID: 0007_similarity_propagation
Revises: 0006_versioned_embeddings
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007_similarity_propagation"
down_revision: str | None = "0006_versioned_embeddings"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)
    op.create_table(
        "sku_hard_pairs",
        sa.Column("first_sku_id", identifier, nullable=False),
        sa.Column("second_sku_id", identifier, nullable=False),
        sa.Column("reason", sa.String(255), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "first_sku_id <> second_sku_id",
            name="ck_sku_hard_pairs_distinct",
        ),
        sa.CheckConstraint(
            "btrim(reason) <> ''",
            name="ck_sku_hard_pairs_reason_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["first_sku_id"],
            ["skus.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["second_sku_id"],
            ["skus.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("first_sku_id", "second_sku_id"),
    )
    op.create_table(
        "propagation_suggestion_sets",
        sa.Column(
            "id",
            identifier,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("seed_annotation_id", identifier, nullable=False),
        sa.Column("seed_annotation_revision", sa.Integer(), nullable=False),
        sa.Column("seed_sku_id", identifier, nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'open'"),
            nullable=False,
        ),
        sa.Column("index_namespace", sa.String(128), nullable=False),
        sa.Column("index_version", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("quality_evidence", sa.String(128), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_by", sa.String(128)),
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.Column("selected_count", sa.Integer()),
        sa.Column("skipped_count", sa.Integer()),
        sa.CheckConstraint(
            "status IN ('open', 'completed')",
            name="ck_propagation_sets_status",
        ),
        sa.CheckConstraint(
            "(status = 'open' AND completed_by IS NULL AND completed_at IS NULL "
            "AND selected_count IS NULL AND skipped_count IS NULL) "
            "OR (status = 'completed' AND completed_by IS NOT NULL "
            "AND completed_at IS NOT NULL AND selected_count >= 0 "
            "AND skipped_count >= 0)",
            name="ck_propagation_sets_completion",
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_propagation_sets_artifact_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["seed_annotation_id", "seed_annotation_revision"],
            ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["seed_sku_id"],
            ["skus.id"],
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "propagation_suggestions",
        sa.Column(
            "id",
            identifier,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("suggestion_set_id", identifier, nullable=False),
        sa.Column("candidate_annotation_id", identifier, nullable=False),
        sa.Column("candidate_annotation_revision", sa.Integer(), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.String(16),
            server_default=sa.text("'suggested'"),
            nullable=False,
        ),
        sa.Column("requires_individual_review", sa.Boolean(), nullable=False),
        sa.Column("risk_reason", sa.String(64)),
        sa.Column("decided_by", sa.String(128)),
        sa.Column("decided_at", sa.DateTime(timezone=True)),
        sa.CheckConstraint(
            "score >= -1 AND score <= 1 AND score <> 'NaN'::double precision",
            name="ck_propagation_suggestions_score",
        ),
        sa.CheckConstraint(
            "status IN ('suggested', 'confirmed', 'skipped')",
            name="ck_propagation_suggestions_status",
        ),
        sa.CheckConstraint(
            "(requires_individual_review AND risk_reason IS NOT NULL) "
            "OR (NOT requires_individual_review AND risk_reason IS NULL)",
            name="ck_propagation_suggestions_risk",
        ),
        sa.CheckConstraint(
            "(status = 'suggested' AND decided_by IS NULL AND decided_at IS NULL) "
            "OR (status IN ('confirmed', 'skipped') "
            "AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
            name="ck_propagation_suggestions_decision",
        ),
        sa.ForeignKeyConstraint(
            ["suggestion_set_id"],
            ["propagation_suggestion_sets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["candidate_annotation_id", "candidate_annotation_revision"],
            ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "suggestion_set_id",
            "candidate_annotation_id",
            name="uq_propagation_suggestions_set_candidate",
        ),
    )
    op.create_index(
        "ix_propagation_suggestions_set_status",
        "propagation_suggestions",
        ["suggestion_set_id", "status"],
    )


def downgrade() -> None:
    op.drop_table("propagation_suggestions")
    op.drop_table("propagation_suggestion_sets")
    op.drop_table("sku_hard_pairs")
