"""Add managed image media ownership fields.

Revision ID: 0003_managed_media
Revises: 0002_canonical_model
Create Date: 2026-07-26
"""

import sqlalchemy as sa
from alembic import op

revision: str = "0003_managed_media"
down_revision: str | None = "0002_canonical_model"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.drop_constraint("ck_images_managed_media_key", "images", type_="check")
    op.add_column("images", sa.Column("original_media_key", sa.String(1024), nullable=False))
    op.add_column("images", sa.Column("thumbnail_media_key", sa.String(1024), nullable=False))
    op.add_column("images", sa.Column("media_type", sa.String(32), nullable=False))
    op.add_column("images", sa.Column("original_filename", sa.String(255), nullable=False))
    op.create_check_constraint(
        "ck_images_managed_media_keys",
        "images",
        "original_media_key <> '' "
        "AND canonical_media_key <> '' "
        "AND thumbnail_media_key <> '' "
        "AND left(original_media_key, 1) <> '/' "
        "AND left(canonical_media_key, 1) <> '/' "
        "AND left(thumbnail_media_key, 1) <> '/' "
        "AND original_media_key !~ '^[A-Za-z]:' "
        "AND canonical_media_key !~ '^[A-Za-z]:' "
        "AND thumbnail_media_key !~ '^[A-Za-z]:' "
        "AND position('..' in original_media_key) = 0 "
        "AND position('..' in canonical_media_key) = 0 "
        "AND position('..' in thumbnail_media_key) = 0 "
        "AND position(chr(92) in original_media_key) = 0 "
        "AND position(chr(92) in canonical_media_key) = 0 "
        "AND position(chr(92) in thumbnail_media_key) = 0",
    )
    op.create_check_constraint(
        "ck_images_media_type",
        "images",
        "media_type IN ('image/jpeg', 'image/png')",
    )
    op.create_check_constraint(
        "ck_images_original_filename",
        "images",
        "btrim(original_filename) <> ''",
    )


def downgrade() -> None:
    op.drop_constraint("ck_images_original_filename", "images", type_="check")
    op.drop_constraint("ck_images_media_type", "images", type_="check")
    op.drop_constraint("ck_images_managed_media_keys", "images", type_="check")
    op.drop_column("images", "original_filename")
    op.drop_column("images", "media_type")
    op.drop_column("images", "thumbnail_media_key")
    op.drop_column("images", "original_media_key")
    op.create_check_constraint(
        "ck_images_managed_media_key",
        "images",
        "canonical_media_key <> '' "
        "AND left(canonical_media_key, 1) <> '/' "
        "AND canonical_media_key !~ '^[A-Za-z]:' "
        "AND position('..' in canonical_media_key) = 0 "
        "AND position(chr(92) in canonical_media_key) = 0",
    )
