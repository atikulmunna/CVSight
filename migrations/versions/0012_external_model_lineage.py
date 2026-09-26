"""Let the model registry hold models trained outside CVSight.

An externally trained model has no CVSight training snapshot, so its entry records
lineage "external" and leaves the training version empty instead of borrowing another
version. Snapshot-trained entries keep their training version, and a check keeps the
two cases apart.

Revision ID: 0012_external_model_lineage
Revises: 0011_authentication
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012_external_model_lineage"
down_revision: str | None = "0011_authentication"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # A server default fills existing rows without firing the immutability trigger.
    op.add_column(
        "model_registry_entries",
        sa.Column(
            "lineage",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'snapshot'"),
        ),
    )
    op.alter_column(
        "model_registry_entries",
        "training_dataset_version_id",
        nullable=True,
    )
    op.create_check_constraint(
        "ck_model_registry_lineage",
        "model_registry_entries",
        "(lineage = 'snapshot' AND training_dataset_version_id IS NOT NULL) "
        "OR (lineage = 'external' AND training_dataset_version_id IS NULL)",
    )


def downgrade() -> None:
    external = op.get_bind().execute(
        sa.text("SELECT count(*) FROM model_registry_entries WHERE lineage = 'external'")
    ).scalar_one()
    if external:
        raise RuntimeError(
            "external model registry entries exist; the registry is append-only, so "
            "this downgrade will not delete them"
        )
    op.drop_constraint("ck_model_registry_lineage", "model_registry_entries", type_="check")
    op.alter_column(
        "model_registry_entries",
        "training_dataset_version_id",
        nullable=False,
    )
    op.drop_column("model_registry_entries", "lineage")
