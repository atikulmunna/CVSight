"""Create the canonical annotation data model.

Revision ID: 0002_canonical_model
Revises: 0001_bootstrap
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_canonical_model"
down_revision: str | None = "0001_bootstrap"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)

    op.create_table(
        "datasets",
        sa.Column("id", identifier, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("btrim(name) <> ''", name="ck_datasets_name_not_blank"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_datasets_name"),
    )
    op.create_table(
        "dataset_versions",
        sa.Column("id", identifier, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("dataset_id", identifier, nullable=False),
        sa.Column("parent_version_id", identifier, nullable=True),
        sa.Column("snapshot_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["datasets.id"],
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "parent_version_id"],
            ["dataset_versions.dataset_id", "dataset_versions.id"],
            name="fk_dataset_versions_parent_same_dataset",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id",
            "id",
            name="uq_dataset_versions_dataset_id_id",
        ),
    )
    op.create_index(
        "uq_dataset_versions_one_open",
        "dataset_versions",
        ["dataset_id"],
        unique=True,
        postgresql_where=sa.text("snapshot_at IS NULL"),
    )
    op.create_table(
        "images",
        sa.Column("id", identifier, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("dataset_id", identifier, nullable=False),
        sa.Column("canonical_media_key", sa.String(length=1024), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("canonical_width", sa.Integer(), nullable=False),
        sa.Column("canonical_height", sa.Integer(), nullable=False),
        sa.Column(
            "capture_metadata",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=32),
            server_default=sa.text("'unlabeled'"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint("canonical_width > 0", name="ck_images_width_positive"),
        sa.CheckConstraint("canonical_height > 0", name="ck_images_height_positive"),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_images_content_sha256",
        ),
        sa.CheckConstraint(
            "canonical_media_key <> '' "
            "AND left(canonical_media_key, 1) <> '/' "
            "AND canonical_media_key !~ '^[A-Za-z]:' "
            "AND position('..' in canonical_media_key) = 0 "
            "AND position(chr(92) in canonical_media_key) = 0",
            name="ck_images_managed_media_key",
        ),
        sa.CheckConstraint(
            "status IN ('unlabeled', 'pre_labeled', 'in_progress', 'labeled', 'reviewed')",
            name="ck_images_status",
        ),
        sa.ForeignKeyConstraint(["dataset_id"], ["datasets.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id",
            "content_sha256",
            name="uq_images_dataset_content",
        ),
        sa.UniqueConstraint("dataset_id", "id", name="uq_images_dataset_id_id"),
    )
    op.create_index(
        "ix_images_dataset_status",
        "images",
        ["dataset_id", "status"],
        unique=False,
    )
    op.create_table(
        "dataset_version_images",
        sa.Column("dataset_id", identifier, nullable=False),
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("image_id", identifier, nullable=False),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "image_id"],
            ["images.dataset_id", "images.id"],
            name="fk_version_images_image_same_dataset",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version_id"],
            ["dataset_versions.dataset_id", "dataset_versions.id"],
            name="fk_version_images_version_same_dataset",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_version_id",
            "image_id",
            name="pk_dataset_version_images",
        ),
        sa.UniqueConstraint(
            "dataset_id",
            "dataset_version_id",
            "image_id",
            name="uq_version_images_dataset_version_image",
        ),
    )
    op.create_table(
        "skus",
        sa.Column("id", identifier, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("upc", sa.String(length=32), nullable=True),
        sa.Column("category", sa.String(length=255), nullable=True),
        sa.Column("subcategory", sa.String(length=255), nullable=True),
        sa.Column("brand", sa.String(length=255), nullable=True),
        sa.Column("variant", sa.String(length=255), nullable=True),
        sa.Column(
            "status",
            sa.String(length=16),
            server_default=sa.text("'active'"),
            nullable=False,
        ),
        sa.Column("merged_into_id", identifier, nullable=True),
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
        sa.CheckConstraint("btrim(name) <> ''", name="ck_skus_name_not_blank"),
        sa.CheckConstraint(
            "(status = 'merged' AND merged_into_id IS NOT NULL AND merged_into_id <> id) "
            "OR (status IN ('active', 'deprecated') AND merged_into_id IS NULL)",
            name="ck_skus_merge_state",
        ),
        sa.ForeignKeyConstraint(["merged_into_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_skus_status_name", "skus", ["status", "name"], unique=False)
    op.create_table(
        "annotations",
        sa.Column("id", identifier, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("image_id", identifier, nullable=False),
        sa.Column(
            "current_revision",
            sa.Integer(),
            server_default=sa.text("1"),
            nullable=False,
        ),
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
        sa.CheckConstraint(
            "current_revision > 0",
            name="ck_annotations_current_revision_positive",
        ),
        sa.ForeignKeyConstraint(["image_id"], ["images.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("image_id", "id", name="uq_annotations_image_id_id"),
    )
    op.create_index("ix_annotations_image", "annotations", ["image_id"], unique=False)
    op.create_table(
        "annotation_revisions",
        sa.Column("annotation_id", identifier, nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
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
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("occluded", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("truncated", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("shelf_row", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision > 0",
            name="ck_annotation_revisions_revision_positive",
        ),
        sa.CheckConstraint(
            "x >= 0 AND x <> 'NaN'::double precision "
            "AND y >= 0 AND y <> 'NaN'::double precision "
            "AND width > 0 AND width <> 'NaN'::double precision "
            "AND height > 0 AND height <> 'NaN'::double precision",
            name="ck_annotation_revisions_geometry",
        ),
        sa.CheckConstraint(
            "class_type IN ('product', 'gap', 'shelf_label')",
            name="ck_annotation_revisions_class_type",
        ),
        sa.CheckConstraint(
            "class_type = 'product' OR sku_id IS NULL",
            name="ck_annotation_revisions_sku_class",
        ),
        sa.CheckConstraint(
            "lifecycle_state IN ('proposed', 'verified', 'rejected')",
            name="ck_annotation_revisions_lifecycle",
        ),
        sa.CheckConstraint(
            "review_state IN ('unreviewed', 'accepted', 'flagged')",
            name="ck_annotation_revisions_review_state",
        ),
        sa.CheckConstraint(
            "source IN ('model', 'human', 'propagated', 'imported')",
            name="ck_annotation_revisions_source",
        ),
        sa.CheckConstraint(
            "confidence IS NULL OR "
            "(confidence >= 0 AND confidence <= 1 "
            "AND confidence <> 'NaN'::double precision)",
            name="ck_annotation_revisions_confidence",
        ),
        sa.CheckConstraint(
            "shelf_row IS NULL OR shelf_row >= 0",
            name="ck_annotation_revisions_shelf_row",
        ),
        sa.ForeignKeyConstraint(["annotation_id"], ["annotations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["sku_id"], ["skus.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("annotation_id", "revision"),
    )
    op.create_index(
        "ix_annotation_revisions_sku",
        "annotation_revisions",
        ["sku_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_annotations_current_revision",
        "annotations",
        "annotation_revisions",
        ["id", "current_revision"],
        ["annotation_id", "revision"],
        deferrable=True,
        initially="DEFERRED",
    )
    op.create_table(
        "dataset_version_annotations",
        sa.Column("dataset_id", identifier, nullable=False),
        sa.Column("dataset_version_id", identifier, nullable=False),
        sa.Column("image_id", identifier, nullable=False),
        sa.Column("annotation_id", identifier, nullable=False),
        sa.Column("annotation_revision", sa.Integer(), nullable=False),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["image_id", "annotation_id"],
            ["annotations.image_id", "annotations.id"],
            name="fk_version_annotations_annotation_image",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id", "dataset_version_id", "image_id"],
            [
                "dataset_version_images.dataset_id",
                "dataset_version_images.dataset_version_id",
                "dataset_version_images.image_id",
            ],
            name="fk_version_annotations_image_membership",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["annotation_id", "annotation_revision"],
            ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
            name="fk_version_annotations_revision",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "dataset_version_id",
            "annotation_id",
            name="pk_dataset_version_annotations",
        ),
    )

    op.execute(
        """
        CREATE FUNCTION shelfsight_check_annotation_geometry()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            image_width integer;
            image_height integer;
        BEGIN
            SELECT images.canonical_width, images.canonical_height
            INTO image_width, image_height
            FROM annotations
            JOIN images ON images.id = annotations.image_id
            WHERE annotations.id = NEW.annotation_id;

            IF NOT FOUND THEN
                RAISE EXCEPTION 'annotation image does not exist'
                    USING ERRCODE = '23503';
            END IF;

            IF NEW.x = 'NaN'::double precision
                OR NEW.y = 'NaN'::double precision
                OR NEW.width = 'NaN'::double precision
                OR NEW.height = 'NaN'::double precision
                OR NEW.x < 0
                OR NEW.y < 0
                OR NEW.width <= 0
                OR NEW.height <= 0
                OR NEW.x + NEW.width > image_width
                OR NEW.y + NEW.height > image_height
            THEN
                RAISE EXCEPTION 'annotation geometry is outside canonical image bounds'
                    USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_annotation_geometry
        BEFORE INSERT OR UPDATE OF x, y, width, height, annotation_id
        ON annotation_revisions
        FOR EACH ROW
        EXECUTE FUNCTION shelfsight_check_annotation_geometry();
        """
    )
    op.execute(
        """
        CREATE FUNCTION shelfsight_reject_frozen_version_change()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF TG_OP <> 'INSERT'
                AND EXISTS (
                    SELECT 1
                    FROM dataset_versions
                    WHERE id = OLD.dataset_version_id
                      AND snapshot_at IS NOT NULL
                )
            THEN
                RAISE EXCEPTION 'dataset version is immutable after snapshot'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP <> 'DELETE'
                AND EXISTS (
                    SELECT 1
                    FROM dataset_versions
                    WHERE id = NEW.dataset_version_id
                      AND snapshot_at IS NOT NULL
                )
            THEN
                RAISE EXCEPTION 'dataset version is immutable after snapshot'
                    USING ERRCODE = '23514';
            END IF;

            IF TG_OP = 'DELETE' THEN
                RETURN OLD;
            END IF;
            RETURN NEW;
        END;
        $$;
        """
    )
    for table_name in ("dataset_version_images", "dataset_version_annotations"):
        op.execute(
            f"""
            CREATE TRIGGER trg_{table_name}_immutable
            BEFORE INSERT OR UPDATE OR DELETE
            ON {table_name}
            FOR EACH ROW
            EXECUTE FUNCTION shelfsight_reject_frozen_version_change();
            """
        )
    op.execute(
        """
        CREATE FUNCTION shelfsight_reject_sku_merge_cycle()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        DECLARE
            next_sku_id uuid;
            visited uuid[] := ARRAY[NEW.id];
        BEGIN
            IF NEW.merged_into_id IS NULL THEN
                RETURN NEW;
            END IF;

            next_sku_id := NEW.merged_into_id;
            LOOP
                IF next_sku_id = ANY(visited) THEN
                    RAISE EXCEPTION 'SKU merge would create a cycle'
                        USING ERRCODE = '23514';
                END IF;
                visited := array_append(visited, next_sku_id);

                SELECT merged_into_id
                INTO next_sku_id
                FROM skus
                WHERE id = next_sku_id;

                IF NOT FOUND OR next_sku_id IS NULL THEN
                    RETURN NEW;
                END IF;
            END LOOP;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE TRIGGER trg_skus_merge_cycle
        BEFORE INSERT OR UPDATE OF merged_into_id, status
        ON skus
        FOR EACH ROW
        EXECUTE FUNCTION shelfsight_reject_sku_merge_cycle();
        """
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_annotations_current_revision",
        "annotations",
        type_="foreignkey",
    )
    op.drop_table("dataset_version_annotations")
    op.drop_index("ix_annotation_revisions_sku", table_name="annotation_revisions")
    op.drop_table("annotation_revisions")
    op.drop_index("ix_annotations_image", table_name="annotations")
    op.drop_table("annotations")
    op.drop_index("ix_skus_status_name", table_name="skus")
    op.drop_table("skus")
    op.drop_table("dataset_version_images")
    op.drop_index("ix_images_dataset_status", table_name="images")
    op.drop_table("images")
    op.drop_index("uq_dataset_versions_one_open", table_name="dataset_versions")
    op.drop_table("dataset_versions")
    op.drop_table("datasets")
    op.execute("DROP FUNCTION shelfsight_reject_sku_merge_cycle()")
    op.execute("DROP FUNCTION shelfsight_reject_frozen_version_change()")
    op.execute("DROP FUNCTION shelfsight_check_annotation_geometry()")
