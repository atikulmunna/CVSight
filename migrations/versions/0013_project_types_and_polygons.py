"""Give projects a type and let product annotations carry an outline.

A project is an object detection project, labeled with boxes, or an instance
segmentation project, whose products also carry a polygon outline. Existing projects
become detection projects. Revisions and snapshot rows gain a nullable polygon; a box
is always kept as the outline's bounds, so box consumers are unaffected.

Revision ID: 0013_project_types_and_polygons
Revises: 0012_external_model_lineage
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013_project_types_and_polygons"
down_revision: str | None = "0012_external_model_lineage"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Server defaults and nullable columns fill existing rows without firing the
    # append-only triggers on revisions and snapshots.
    op.add_column(
        "datasets",
        sa.Column(
            "project_type",
            sa.String(length=32),
            nullable=False,
            server_default=sa.text("'object_detection'"),
        ),
    )
    op.create_check_constraint(
        "ck_datasets_project_type",
        "datasets",
        "project_type IN ('object_detection', 'instance_segmentation')",
    )
    op.add_column(
        "annotation_revisions",
        sa.Column("polygon", postgresql.JSONB(astext_type=sa.Text())),
    )
    op.create_check_constraint(
        "ck_annotation_revisions_polygon_class",
        "annotation_revisions",
        "polygon IS NULL OR class_type = 'product'",
    )
    op.create_check_constraint(
        "ck_annotation_revisions_polygon_points",
        "annotation_revisions",
        "polygon IS NULL OR (jsonb_typeof(polygon) = 'array' "
        "AND jsonb_array_length(polygon) BETWEEN 3 AND 1000)",
    )
    op.add_column(
        "dataset_snapshot_annotations",
        sa.Column("polygon", postgresql.JSONB(astext_type=sa.Text())),
    )


def downgrade() -> None:
    bind = op.get_bind()
    outlines = bind.execute(
        sa.text("SELECT count(*) FROM annotation_revisions WHERE polygon IS NOT NULL")
    ).scalar_one()
    segmentation = bind.execute(
        sa.text(
            "SELECT count(*) FROM datasets WHERE project_type = 'instance_segmentation'"
        )
    ).scalar_one()
    if outlines or segmentation:
        raise RuntimeError(
            "instance segmentation projects or annotation outlines exist; revisions are "
            "append-only, so this downgrade will not delete them"
        )
    op.drop_column("dataset_snapshot_annotations", "polygon")
    op.drop_constraint(
        "ck_annotation_revisions_polygon_points", "annotation_revisions", type_="check"
    )
    op.drop_constraint(
        "ck_annotation_revisions_polygon_class", "annotation_revisions", type_="check"
    )
    op.drop_column("annotation_revisions", "polygon")
    op.drop_constraint("ck_datasets_project_type", "datasets", type_="check")
    op.drop_column("datasets", "project_type")
