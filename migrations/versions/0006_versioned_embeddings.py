"""Add versioned pgvector embeddings.

Revision ID: 0006_versioned_embeddings
Revises: 0005_durable_jobs
Create Date: 2026-07-28
"""

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import VECTOR
from sqlalchemy.dialects import postgresql

revision: str = "0006_versioned_embeddings"
down_revision: str | None = "0005_durable_jobs"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)
    document = postgresql.JSONB(astext_type=sa.Text())
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.create_table(
        "embeddings",
        sa.Column("id", identifier, nullable=False),
        sa.Column("purpose", sa.String(16), nullable=False),
        sa.Column("subject_type", sa.String(24), nullable=False),
        sa.Column("annotation_id", identifier),
        sa.Column("annotation_revision", sa.Integer()),
        sa.Column("reference_image_id", identifier),
        sa.Column("target_fingerprint", sa.String(128), nullable=False),
        sa.Column("model_id", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("artifact_sha256", sa.String(64), nullable=False),
        sa.Column("configuration", document, nullable=False),
        sa.Column("dimension", sa.Integer(), nullable=False),
        sa.Column("embedding", VECTOR(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "purpose IN ('recognition', 'propagation')",
            name="ck_embeddings_purpose",
        ),
        sa.CheckConstraint(
            "subject_type IN ('annotation', 'sku_reference')",
            name="ck_embeddings_subject_type",
        ),
        sa.CheckConstraint(
            "(subject_type = 'annotation' "
            "AND annotation_id IS NOT NULL "
            "AND annotation_revision IS NOT NULL "
            "AND reference_image_id IS NULL) "
            "OR (subject_type = 'sku_reference' "
            "AND annotation_id IS NULL "
            "AND annotation_revision IS NULL "
            "AND reference_image_id IS NOT NULL)",
            name="ck_embeddings_subject",
        ),
        sa.CheckConstraint(
            "dimension BETWEEN 1 AND 4096 AND vector_dims(embedding) = dimension",
            name="ck_embeddings_dimension",
        ),
        sa.CheckConstraint(
            "target_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
            name="ck_embeddings_target_fingerprint",
        ),
        sa.CheckConstraint(
            "artifact_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_embeddings_artifact_sha256",
        ),
        sa.ForeignKeyConstraint(
            ["annotation_id", "annotation_revision"],
            ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["reference_image_id"],
            ["sku_reference_images.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "uq_embeddings_annotation_version",
        "embeddings",
        [
            "purpose",
            "annotation_id",
            "annotation_revision",
            "target_fingerprint",
            "model_id",
            "model_version",
            "artifact_sha256",
        ],
        unique=True,
        postgresql_where=sa.text("subject_type = 'annotation'"),
    )
    op.create_index(
        "uq_embeddings_reference_version",
        "embeddings",
        [
            "purpose",
            "reference_image_id",
            "target_fingerprint",
            "model_id",
            "model_version",
            "artifact_sha256",
        ],
        unique=True,
        postgresql_where=sa.text("subject_type = 'sku_reference'"),
    )
    op.create_index(
        "ix_embeddings_recognition_gallery",
        "embeddings",
        ["purpose", "model_id", "model_version", "artifact_sha256"],
        postgresql_where=sa.text("subject_type = 'sku_reference'"),
    )


def downgrade() -> None:
    op.drop_table("embeddings")
