"""Add immutable dataset snapshot state and lineage.

Revision ID: 0008_immutable_snapshot_lineage
Revises: 0007_similarity_propagation
Create Date: 2026-08-07
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008_immutable_snapshot_lineage"
down_revision: str | None = "0007_similarity_propagation"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)
    json_document = postgresql.JSONB(astext_type=sa.Text())

    op.create_table(
        "dataset_snapshots",
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("dataset_id", identifier, nullable=False),
        sa.Column("parent_version_id", identifier, nullable=True),
        sa.Column("schema_version", sa.String(length=64), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_versions.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["parent_version_id"],
            ["dataset_snapshots.dataset_version_id"],
            name="fk_dataset_snapshots_parent",
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_snapshots_sha256",
        ),
        sa.CheckConstraint(
            "btrim(schema_version) <> ''",
            name="ck_dataset_snapshots_schema_version",
        ),
        sa.PrimaryKeyConstraint("dataset_version_id"),
        sa.UniqueConstraint(
            "dataset_id",
            "dataset_version_id",
            name="uq_dataset_snapshots_dataset_version",
        ),
    )
    op.create_table(
        "dataset_snapshot_images",
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("image_id", identifier, nullable=False),
        sa.Column("canonical_media_key", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_width", sa.Integer(), nullable=False),
        sa.Column("canonical_height", sa.Integer(), nullable=False),
        sa.Column(
            "capture_metadata",
            json_document,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'unlabeled'"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_snapshots.dataset_version_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_snapshot_images_sha256",
        ),
        sa.CheckConstraint(
            "canonical_width > 0 AND canonical_height > 0",
            name="ck_dataset_snapshot_images_dimensions",
        ),
        sa.CheckConstraint(
            "status IN ('unlabeled', 'pre_labeled', 'in_progress', 'labeled', 'reviewed')",
            name="ck_dataset_snapshot_images_status",
        ),
        sa.PrimaryKeyConstraint("dataset_version_id", "image_id"),
    )
    op.create_table(
        "dataset_snapshot_skus",
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("sku_id", identifier, nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("upc", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("subcategory", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("variant", sa.String(length=255), nullable=True),
        sa.Column("is_unknown", sa.Boolean(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("merged_into_id", identifier, nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_snapshots.dataset_version_id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "merged_into_id"],
            [
                "dataset_snapshot_skus.dataset_version_id",
                "dataset_snapshot_skus.sku_id",
            ],
            name="fk_dataset_snapshot_skus_merge_target",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_dataset_snapshot_skus_name"),
        sa.CheckConstraint(
            "status IN ('active', 'deprecated', 'merged')",
            name="ck_dataset_snapshot_skus_status",
        ),
        sa.PrimaryKeyConstraint("dataset_version_id", "sku_id"),
    )
    op.create_table(
        "dataset_snapshot_sku_references",
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("reference_image_id", identifier, nullable=False),
        sa.Column("sku_id", identifier, nullable=False),
        sa.Column("canonical_media_key", sa.String(length=1024), nullable=False),
        sa.Column("media_type", sa.String(length=32), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "sku_id"],
            [
                "dataset_snapshot_skus.dataset_version_id",
                "dataset_snapshot_skus.sku_id",
            ],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_dataset_snapshot_sku_references_sha256",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0",
            name="ck_dataset_snapshot_sku_references_dimensions",
        ),
        sa.PrimaryKeyConstraint("dataset_version_id", "reference_image_id"),
    )
    op.create_table(
        "dataset_snapshot_annotations",
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("annotation_id", identifier, nullable=False),
        sa.Column("annotation_revision", sa.Integer(), nullable=False),
        sa.Column("image_id", identifier, nullable=False),
        sa.Column("x", sa.Float(), nullable=False),
        sa.Column("y", sa.Float(), nullable=False),
        sa.Column("width", sa.Float(), nullable=False),
        sa.Column("height", sa.Float(), nullable=False),
        sa.Column("class_type", sa.String(length=24), nullable=False),
        sa.Column("sku_id", identifier, nullable=True),
        sa.Column("lifecycle_state", sa.String(length=16), nullable=False),
        sa.Column("review_state", sa.String(length=16), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "provenance",
            json_document,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("occluded", sa.Boolean(), nullable=False),
        sa.Column("truncated", sa.Boolean(), nullable=False),
        sa.Column("shelf_row", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "image_id"],
            [
                "dataset_snapshot_images.dataset_version_id",
                "dataset_snapshot_images.image_id",
            ],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id", "sku_id"],
            [
                "dataset_snapshot_skus.dataset_version_id",
                "dataset_snapshot_skus.sku_id",
            ],
            ondelete="RESTRICT",
        ),
        sa.CheckConstraint(
            "annotation_revision > 0",
            name="ck_dataset_snapshot_annotations_revision",
        ),
        sa.PrimaryKeyConstraint("dataset_version_id", "annotation_id"),
    )
    op.create_table(
        "snapshot_artifacts",
        sa.Column(
            "id",
            identifier,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("artifact_type", sa.String(length=16), nullable=False),
        sa.Column("artifact_key", sa.String(length=255), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
        sa.Column(
            "metadata",
            json_document,
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_version_id"],
            ["dataset_snapshots.dataset_version_id"],
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "artifact_type IN ('export', 'model', 'evaluation')",
            name="ck_snapshot_artifacts_type",
        ),
        sa.CheckConstraint(
            "btrim(artifact_key) <> ''",
            name="ck_snapshot_artifacts_key",
        ),
        sa.CheckConstraint(
            "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_snapshot_artifacts_sha256",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "artifact_type",
            "artifact_key",
            name="uq_snapshot_artifacts_identity",
        ),
    )
    op.add_column("jobs", sa.Column("dataset_version_id", identifier, nullable=True))
    op.create_foreign_key(
        "fk_jobs_dataset_snapshot",
        "jobs",
        "dataset_snapshots",
        ["dataset_version_id"],
        ["dataset_version_id"],
        ondelete="RESTRICT",
    )

    op.execute(
        """
        CREATE FUNCTION shelfsight_reject_snapshot_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND pg_trigger_depth() > 1 THEN
                RETURN OLD;
            END IF;
            RAISE EXCEPTION 'snapshot state is immutable'
                USING ERRCODE = '23514';
        END;
        $$;
        """
    )
    for table_name in (
        "dataset_snapshots",
        "dataset_snapshot_images",
        "dataset_snapshot_annotations",
        "dataset_snapshot_skus",
        "dataset_snapshot_sku_references",
        "snapshot_artifacts",
    ):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE UPDATE OR DELETE
            ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION shelfsight_reject_snapshot_change();
            """
        )
    op.execute(
        """
        CREATE FUNCTION shelfsight_require_snapshot_parent()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.parent_version_id IS NOT NULL
                AND NOT EXISTS (
                    SELECT 1
                    FROM dataset_snapshots
                    WHERE dataset_version_id = NEW.parent_version_id
                      AND dataset_id = NEW.dataset_id
                )
            THEN
                RAISE EXCEPTION 'parent version must be an immutable snapshot'
                    USING ERRCODE = '23503';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_dataset_versions_snapshot_parent
        BEFORE INSERT OR UPDATE OF parent_version_id, dataset_id
        ON dataset_versions
        FOR EACH ROW
        EXECUTE FUNCTION shelfsight_require_snapshot_parent();
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER trg_dataset_versions_snapshot_parent ON dataset_versions")
    op.execute("DROP FUNCTION shelfsight_require_snapshot_parent()")
    for table_name in (
        "snapshot_artifacts",
        "dataset_snapshot_sku_references",
        "dataset_snapshot_skus",
        "dataset_snapshot_annotations",
        "dataset_snapshot_images",
        "dataset_snapshots",
    ):
        op.execute(f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}")
    op.execute("DROP FUNCTION shelfsight_reject_snapshot_change()")
    op.drop_constraint("fk_jobs_dataset_snapshot", "jobs", type_="foreignkey")
    op.drop_column("jobs", "dataset_version_id")
    op.drop_table("snapshot_artifacts")
    op.drop_table("dataset_snapshot_annotations")
    op.drop_table("dataset_snapshot_sku_references")
    op.drop_table("dataset_snapshot_skus")
    op.drop_table("dataset_snapshot_images")
    op.drop_table("dataset_snapshots")
