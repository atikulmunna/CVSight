"""Add SKU catalog invariants and reference images.

Revision ID: 0004_sku_catalog
Revises: 0003_managed_media
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_sku_catalog"
down_revision: str | None = "0003_managed_media"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    identifier = postgresql.UUID(as_uuid=True)
    op.add_column(
        "skus",
        sa.Column(
            "is_unknown",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_skus_upc_format",
        "skus",
        "upc IS NULL OR upc ~ '^(\\d{8}|\\d{12}|\\d{13}|\\d{14})$'",
    )
    op.create_check_constraint(
        "ck_skus_unknown_state",
        "skus",
        "NOT is_unknown OR (status = 'active' AND merged_into_id IS NULL AND upc IS NULL)",
    )
    op.create_unique_constraint("uq_skus_upc", "skus", ["upc"])
    op.create_index(
        "uq_skus_single_unknown",
        "skus",
        ["is_unknown"],
        unique=True,
        postgresql_where=sa.text("is_unknown"),
    )
    op.create_table(
        "sku_reference_images",
        sa.Column(
            "id",
            identifier,
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("sku_id", identifier, nullable=False),
        sa.Column("original_media_key", sa.String(1024), nullable=False),
        sa.Column("canonical_media_key", sa.String(1024), nullable=False),
        sa.Column("thumbnail_media_key", sa.String(1024), nullable=False),
        sa.Column("media_type", sa.String(32), nullable=False),
        sa.Column("original_filename", sa.String(255), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "original_media_key <> '' AND canonical_media_key <> '' "
            "AND thumbnail_media_key <> '' "
            "AND left(original_media_key, 1) <> '/' "
            "AND left(canonical_media_key, 1) <> '/' "
            "AND left(thumbnail_media_key, 1) <> '/' "
            "AND position('..' in original_media_key) = 0 "
            "AND position('..' in canonical_media_key) = 0 "
            "AND position('..' in thumbnail_media_key) = 0 "
            "AND position(chr(92) in original_media_key) = 0 "
            "AND position(chr(92) in canonical_media_key) = 0 "
            "AND position(chr(92) in thumbnail_media_key) = 0",
            name="ck_sku_reference_images_managed_keys",
        ),
        sa.CheckConstraint(
            "media_type IN ('image/jpeg', 'image/png')",
            name="ck_sku_reference_images_media_type",
        ),
        sa.CheckConstraint(
            "btrim(original_filename) <> ''",
            name="ck_sku_reference_images_filename",
        ),
        sa.CheckConstraint(
            "content_sha256 ~ '^[0-9a-f]{64}$'",
            name="ck_sku_reference_images_sha256",
        ),
        sa.CheckConstraint(
            "width > 0 AND height > 0",
            name="ck_sku_reference_images_dimensions",
        ),
        sa.ForeignKeyConstraint(
            ["sku_id"],
            ["skus.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "sku_id",
            "content_sha256",
            name="uq_sku_reference_images_sku_content",
        ),
    )
    op.create_index(
        "ix_sku_reference_images_sku",
        "sku_reference_images",
        ["sku_id"],
    )
    op.execute(
        """
        INSERT INTO skus (id, name, is_unknown, status)
        VALUES (
            '00000000-0000-0000-0000-000000000001',
            'Unknown / Other',
            true,
            'active'
        )
        ON CONFLICT (id) DO UPDATE
        SET name = EXCLUDED.name,
            upc = NULL,
            is_unknown = true,
            status = 'active',
            merged_into_id = NULL
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DELETE FROM skus
        WHERE id = '00000000-0000-0000-0000-000000000001'
          AND is_unknown = true
        """
    )
    op.drop_table("sku_reference_images")
    op.drop_index("uq_skus_single_unknown", table_name="skus")
    op.drop_constraint("uq_skus_upc", "skus", type_="unique")
    op.drop_constraint("ck_skus_unknown_state", "skus", type_="check")
    op.drop_constraint("ck_skus_upc_format", "skus", type_="check")
    op.drop_column("skus", "is_unknown")
