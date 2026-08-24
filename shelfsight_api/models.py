from __future__ import annotations

from pgvector.sqlalchemy import VECTOR
from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    Column,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    MetaData,
    PrimaryKeyConstraint,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

metadata = MetaData()

identifier = UUID(as_uuid=True)
json_document = JSON().with_variant(JSONB(), "postgresql")

datasets = Table(
    "datasets",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", String(255), nullable=False),
    Column("description", Text),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("btrim(name) <> ''", name="ck_datasets_name_not_blank"),
    UniqueConstraint("name", name="uq_datasets_name"),
)

dataset_versions = Table(
    "dataset_versions",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("dataset_id", identifier, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("parent_version_id", identifier),
    Column("snapshot_at", DateTime(timezone=True)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["dataset_id", "parent_version_id"],
        ["dataset_versions.dataset_id", "dataset_versions.id"],
        name="fk_dataset_versions_parent_same_dataset",
        ondelete="RESTRICT",
    ),
    UniqueConstraint("dataset_id", "id", name="uq_dataset_versions_dataset_id_id"),
)

Index(
    "uq_dataset_versions_one_open",
    dataset_versions.c.dataset_id,
    unique=True,
    postgresql_where=dataset_versions.c.snapshot_at.is_(None),
)

images = Table(
    "images",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("dataset_id", identifier, ForeignKey("datasets.id", ondelete="CASCADE"), nullable=False),
    Column("original_media_key", String(1024), nullable=False),
    Column("canonical_media_key", String(1024), nullable=False),
    Column("thumbnail_media_key", String(1024), nullable=False),
    Column("media_type", String(32), nullable=False),
    Column("original_filename", String(255), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("canonical_width", Integer, nullable=False),
    Column("canonical_height", Integer, nullable=False),
    Column("capture_metadata", json_document, nullable=False, server_default=text("'{}'::jsonb")),
    Column("status", String(32), nullable=False, server_default=text("'unlabeled'")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("canonical_width > 0", name="ck_images_width_positive"),
    CheckConstraint("canonical_height > 0", name="ck_images_height_positive"),
    CheckConstraint(
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_images_content_sha256",
    ),
    CheckConstraint(
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
        name="ck_images_managed_media_keys",
    ),
    CheckConstraint(
        "media_type IN ('image/jpeg', 'image/png')",
        name="ck_images_media_type",
    ),
    CheckConstraint("btrim(original_filename) <> ''", name="ck_images_original_filename"),
    CheckConstraint(
        "status IN ('unlabeled', 'pre_labeled', 'in_progress', 'labeled', 'reviewed')",
        name="ck_images_status",
    ),
    UniqueConstraint("dataset_id", "id", name="uq_images_dataset_id_id"),
    UniqueConstraint("dataset_id", "content_sha256", name="uq_images_dataset_content"),
)

Index("ix_images_dataset_status", images.c.dataset_id, images.c.status)

dataset_version_images = Table(
    "dataset_version_images",
    metadata,
    Column("dataset_id", identifier, nullable=False),
    Column("dataset_version_id", identifier, nullable=False),
    Column("image_id", identifier, nullable=False),
    Column("added_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["dataset_id", "dataset_version_id"],
        ["dataset_versions.dataset_id", "dataset_versions.id"],
        name="fk_version_images_version_same_dataset",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["dataset_id", "image_id"],
        ["images.dataset_id", "images.id"],
        name="fk_version_images_image_same_dataset",
        ondelete="CASCADE",
    ),
    PrimaryKeyConstraint(
        "dataset_version_id",
        "image_id",
        name="pk_dataset_version_images",
    ),
    UniqueConstraint(
        "dataset_id",
        "dataset_version_id",
        "image_id",
        name="uq_version_images_dataset_version_image",
    ),
)

skus = Table(
    "skus",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("name", String(255), nullable=False),
    Column("upc", String(32)),
    Column("category", String(255)),
    Column("subcategory", String(255)),
    Column("brand", String(255)),
    Column("variant", String(255)),
    Column("is_unknown", Boolean, nullable=False, server_default=text("false")),
    Column("status", String(16), nullable=False, server_default=text("'active'")),
    Column("merged_into_id", identifier, ForeignKey("skus.id", ondelete="RESTRICT")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column(
        "updated_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        server_onupdate=func.now(),
    ),
    CheckConstraint("btrim(name) <> ''", name="ck_skus_name_not_blank"),
    CheckConstraint(
        "upc IS NULL OR upc ~ '^(\\d{8}|\\d{12}|\\d{13}|\\d{14})$'",
        name="ck_skus_upc_format",
    ),
    CheckConstraint(
        "NOT is_unknown OR (status = 'active' AND merged_into_id IS NULL AND upc IS NULL)",
        name="ck_skus_unknown_state",
    ),
    CheckConstraint(
        "(status = 'merged' AND merged_into_id IS NOT NULL AND merged_into_id <> id) "
        "OR (status IN ('active', 'deprecated') AND merged_into_id IS NULL)",
        name="ck_skus_merge_state",
    ),
    UniqueConstraint("upc", name="uq_skus_upc"),
)

Index("ix_skus_status_name", skus.c.status, skus.c.name)
Index(
    "uq_skus_single_unknown",
    skus.c.is_unknown,
    unique=True,
    postgresql_where=skus.c.is_unknown.is_(True),
)

sku_reference_images = Table(
    "sku_reference_images",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column(
        "sku_id",
        identifier,
        ForeignKey("skus.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("original_media_key", String(1024), nullable=False),
    Column("canonical_media_key", String(1024), nullable=False),
    Column("thumbnail_media_key", String(1024), nullable=False),
    Column("media_type", String(32), nullable=False),
    Column("original_filename", String(255), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
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
    CheckConstraint(
        "media_type IN ('image/jpeg', 'image/png')",
        name="ck_sku_reference_images_media_type",
    ),
    CheckConstraint(
        "btrim(original_filename) <> ''",
        name="ck_sku_reference_images_filename",
    ),
    CheckConstraint(
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_sku_reference_images_sha256",
    ),
    CheckConstraint(
        "width > 0 AND height > 0",
        name="ck_sku_reference_images_dimensions",
    ),
    UniqueConstraint(
        "sku_id",
        "content_sha256",
        name="uq_sku_reference_images_sku_content",
    ),
)

Index("ix_sku_reference_images_sku", sku_reference_images.c.sku_id)

annotation_records = Table(
    "annotations",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("image_id", identifier, ForeignKey("images.id", ondelete="CASCADE"), nullable=False),
    Column("current_revision", Integer, nullable=False, server_default=text("1")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("current_revision > 0", name="ck_annotations_current_revision_positive"),
    UniqueConstraint("image_id", "id", name="uq_annotations_image_id_id"),
)

annotation_revisions = Table(
    "annotation_revisions",
    metadata,
    Column(
        "annotation_id",
        identifier,
        ForeignKey("annotations.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("revision", Integer, primary_key=True),
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False),
    Column("width", Float, nullable=False),
    Column("height", Float, nullable=False),
    Column("class_type", String(24), nullable=False),
    Column("sku_id", identifier, ForeignKey("skus.id", ondelete="RESTRICT")),
    Column("lifecycle_state", String(16), nullable=False),
    Column("review_state", String(16), nullable=False),
    Column("source", String(16), nullable=False),
    Column("provenance", json_document, nullable=False, server_default=text("'{}'::jsonb")),
    Column("confidence", Float),
    Column("occluded", Boolean, nullable=False, server_default=text("false")),
    Column("truncated", Boolean, nullable=False, server_default=text("false")),
    Column("shelf_row", Integer),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint("revision > 0", name="ck_annotation_revisions_revision_positive"),
    CheckConstraint(
        "x >= 0 AND x <> 'NaN'::double precision "
        "AND y >= 0 AND y <> 'NaN'::double precision "
        "AND width > 0 AND width <> 'NaN'::double precision "
        "AND height > 0 AND height <> 'NaN'::double precision",
        name="ck_annotation_revisions_geometry",
    ),
    CheckConstraint(
        "class_type IN ('product', 'gap', 'shelf_label')",
        name="ck_annotation_revisions_class_type",
    ),
    CheckConstraint(
        "class_type = 'product' OR sku_id IS NULL",
        name="ck_annotation_revisions_sku_class",
    ),
    CheckConstraint(
        "lifecycle_state IN ('proposed', 'verified', 'rejected')",
        name="ck_annotation_revisions_lifecycle",
    ),
    CheckConstraint(
        "review_state IN ('unreviewed', 'accepted', 'flagged')",
        name="ck_annotation_revisions_review_state",
    ),
    CheckConstraint(
        "source IN ('model', 'human', 'propagated', 'imported')",
        name="ck_annotation_revisions_source",
    ),
    CheckConstraint(
        "confidence IS NULL OR "
        "(confidence >= 0 AND confidence <= 1 "
        "AND confidence <> 'NaN'::double precision)",
        name="ck_annotation_revisions_confidence",
    ),
    CheckConstraint(
        "shelf_row IS NULL OR shelf_row >= 0",
        name="ck_annotation_revisions_shelf_row",
    ),
)

annotation_records.append_constraint(
    ForeignKeyConstraint(
        ["id", "current_revision"],
        ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
        name="fk_annotations_current_revision",
        deferrable=True,
        initially="DEFERRED",
        use_alter=True,
    )
)

Index("ix_annotations_image", annotation_records.c.image_id)
Index("ix_annotation_revisions_sku", annotation_revisions.c.sku_id)

dataset_version_annotations = Table(
    "dataset_version_annotations",
    metadata,
    Column("dataset_id", identifier, nullable=False),
    Column("dataset_version_id", identifier, nullable=False),
    Column("image_id", identifier, nullable=False),
    Column("annotation_id", identifier, nullable=False),
    Column("annotation_revision", Integer, nullable=False),
    Column("captured_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["dataset_id", "dataset_version_id", "image_id"],
        [
            "dataset_version_images.dataset_id",
            "dataset_version_images.dataset_version_id",
            "dataset_version_images.image_id",
        ],
        name="fk_version_annotations_image_membership",
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["image_id", "annotation_id"],
        ["annotations.image_id", "annotations.id"],
        name="fk_version_annotations_annotation_image",
        ondelete="RESTRICT",
    ),
    ForeignKeyConstraint(
        ["annotation_id", "annotation_revision"],
        ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
        name="fk_version_annotations_revision",
        ondelete="RESTRICT",
    ),
    PrimaryKeyConstraint(
        "dataset_version_id",
        "annotation_id",
        name="pk_dataset_version_annotations",
    ),
)

dataset_snapshots = Table(
    "dataset_snapshots",
    metadata,
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("dataset_id", identifier, nullable=False),
    Column(
        "parent_version_id",
        identifier,
        ForeignKey(
            "dataset_snapshots.dataset_version_id",
            name="fk_dataset_snapshots_parent",
            ondelete="RESTRICT",
        ),
    ),
    Column("schema_version", String(64), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("snapshot_at", DateTime(timezone=True), nullable=False),
    CheckConstraint(
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_dataset_snapshots_sha256",
    ),
    CheckConstraint(
        "btrim(schema_version) <> ''",
        name="ck_dataset_snapshots_schema_version",
    ),
    UniqueConstraint(
        "dataset_id",
        "dataset_version_id",
        name="uq_dataset_snapshots_dataset_version",
    ),
)

dataset_snapshot_images = Table(
    "dataset_snapshot_images",
    metadata,
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("image_id", identifier, nullable=False),
    Column("canonical_media_key", String(1024), nullable=False),
    Column("media_type", String(32), nullable=False),
    Column("original_filename", String(255), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("canonical_width", Integer, nullable=False),
    Column("canonical_height", Integer, nullable=False),
    Column(
        "capture_metadata",
        json_document,
        nullable=False,
        server_default=text("'{}'::jsonb"),
    ),
    Column("status", String(32), nullable=False, server_default=text("'unlabeled'")),
    CheckConstraint(
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_dataset_snapshot_images_sha256",
    ),
    CheckConstraint(
        "canonical_width > 0 AND canonical_height > 0",
        name="ck_dataset_snapshot_images_dimensions",
    ),
    CheckConstraint(
        "status IN ('unlabeled', 'pre_labeled', 'in_progress', 'labeled', 'reviewed')",
        name="ck_dataset_snapshot_images_status",
    ),
    PrimaryKeyConstraint("dataset_version_id", "image_id"),
)

dataset_snapshot_skus = Table(
    "dataset_snapshot_skus",
    metadata,
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("sku_id", identifier, nullable=False),
    Column("name", String(255), nullable=False),
    Column("upc", String(32)),
    Column("category", String(255)),
    Column("subcategory", String(255)),
    Column("brand", String(255)),
    Column("variant", String(255)),
    Column("is_unknown", Boolean, nullable=False),
    Column("status", String(16), nullable=False),
    Column("merged_into_id", identifier),
    ForeignKeyConstraint(
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
    CheckConstraint("btrim(name) <> ''", name="ck_dataset_snapshot_skus_name"),
    CheckConstraint(
        "status IN ('active', 'deprecated', 'merged')",
        name="ck_dataset_snapshot_skus_status",
    ),
    PrimaryKeyConstraint("dataset_version_id", "sku_id"),
)

dataset_snapshot_sku_references = Table(
    "dataset_snapshot_sku_references",
    metadata,
    Column("dataset_version_id", identifier, nullable=False),
    Column("reference_image_id", identifier, nullable=False),
    Column("sku_id", identifier, nullable=False),
    Column("canonical_media_key", String(1024), nullable=False),
    Column("media_type", String(32), nullable=False),
    Column("original_filename", String(255), nullable=False),
    Column("content_sha256", String(64), nullable=False),
    Column("width", Integer, nullable=False),
    Column("height", Integer, nullable=False),
    ForeignKeyConstraint(
        ["dataset_version_id", "sku_id"],
        [
            "dataset_snapshot_skus.dataset_version_id",
            "dataset_snapshot_skus.sku_id",
        ],
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_dataset_snapshot_sku_references_sha256",
    ),
    CheckConstraint(
        "width > 0 AND height > 0",
        name="ck_dataset_snapshot_sku_references_dimensions",
    ),
    PrimaryKeyConstraint("dataset_version_id", "reference_image_id"),
)

dataset_snapshot_annotations = Table(
    "dataset_snapshot_annotations",
    metadata,
    Column("dataset_version_id", identifier, nullable=False),
    Column("annotation_id", identifier, nullable=False),
    Column("annotation_revision", Integer, nullable=False),
    Column("image_id", identifier, nullable=False),
    Column("x", Float, nullable=False),
    Column("y", Float, nullable=False),
    Column("width", Float, nullable=False),
    Column("height", Float, nullable=False),
    Column("class_type", String(24), nullable=False),
    Column("sku_id", identifier),
    Column("lifecycle_state", String(16), nullable=False),
    Column("review_state", String(16), nullable=False),
    Column("source", String(16), nullable=False),
    Column("provenance", json_document, nullable=False, server_default=text("'{}'::jsonb")),
    Column("confidence", Float),
    Column("occluded", Boolean, nullable=False),
    Column("truncated", Boolean, nullable=False),
    Column("shelf_row", Integer),
    ForeignKeyConstraint(
        ["dataset_version_id", "image_id"],
        [
            "dataset_snapshot_images.dataset_version_id",
            "dataset_snapshot_images.image_id",
        ],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["dataset_version_id", "sku_id"],
        [
            "dataset_snapshot_skus.dataset_version_id",
            "dataset_snapshot_skus.sku_id",
        ],
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "annotation_revision > 0",
        name="ck_dataset_snapshot_annotations_revision",
    ),
    PrimaryKeyConstraint("dataset_version_id", "annotation_id"),
)

snapshot_artifacts = Table(
    "snapshot_artifacts",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("artifact_type", String(16), nullable=False),
    Column("artifact_key", String(255), nullable=False),
    Column("content_sha256", String(64)),
    Column("metadata", json_document, nullable=False, server_default=text("'{}'::jsonb")),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "artifact_type IN ('export', 'model', 'evaluation')",
        name="ck_snapshot_artifacts_type",
    ),
    CheckConstraint("btrim(artifact_key) <> ''", name="ck_snapshot_artifacts_key"),
    CheckConstraint(
        "content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_snapshot_artifacts_sha256",
    ),
    UniqueConstraint(
        "artifact_type",
        "artifact_key",
        name="uq_snapshot_artifacts_identity",
    ),
)

model_registry_entries = Table(
    "model_registry_entries",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("model_role", String(64), nullable=False),
    Column("model_id", String(128), nullable=False),
    Column("model_version", String(128), nullable=False),
    Column(
        "model_artifact_id",
        identifier,
        ForeignKey("snapshot_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "evaluation_artifact_id",
        identifier,
        ForeignKey("snapshot_artifacts.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "training_dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "evaluation_dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("model_artifact_sha256", String(64), nullable=False),
    Column("evaluation_artifact_sha256", String(64), nullable=False),
    Column("configuration", json_document, nullable=False),
    Column("compatibility", json_document, nullable=False),
    Column("metrics", json_document, nullable=False),
    Column("registered_by", String(128), nullable=False),
    Column("registered_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "model_role ~ '^[a-z][a-z0-9_]{0,63}$'",
        name="ck_model_registry_role_format",
    ),
    CheckConstraint(
        "btrim(model_id) <> '' AND btrim(model_version) <> ''",
        name="ck_model_registry_identity_not_blank",
    ),
    CheckConstraint(
        "model_artifact_sha256 ~ '^[0-9a-f]{64}$' "
        "AND evaluation_artifact_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_model_registry_artifact_sha256",
    ),
    CheckConstraint(
        "btrim(registered_by) <> ''",
        name="ck_model_registry_actor_not_blank",
    ),
    UniqueConstraint(
        "model_role",
        "model_id",
        "model_version",
        name="uq_model_registry_identity",
    ),
    UniqueConstraint(
        "model_role",
        "model_artifact_sha256",
        name="uq_model_registry_role_artifact",
    ),
)

Index(
    "ix_model_registry_role_registered",
    model_registry_entries.c.model_role,
    model_registry_entries.c.registered_at,
)

model_deployments = Table(
    "model_deployments",
    metadata,
    Column("model_role", String(64), primary_key=True),
    Column(
        "active_entry_id",
        identifier,
        ForeignKey("model_registry_entries.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column(
        "previous_entry_id",
        identifier,
        ForeignKey("model_registry_entries.id", ondelete="SET NULL"),
    ),
    Column("updated_by", String(128), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "model_role ~ '^[a-z][a-z0-9_]{0,63}$'",
        name="ck_model_deployments_role_format",
    ),
    CheckConstraint(
        "previous_entry_id IS NULL OR previous_entry_id <> active_entry_id",
        name="ck_model_deployments_distinct_entries",
    ),
    CheckConstraint(
        "btrim(updated_by) <> ''",
        name="ck_model_deployments_actor_not_blank",
    ),
)

model_deployment_events = Table(
    "model_deployment_events",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("model_role", String(64), nullable=False),
    Column(
        "from_entry_id",
        identifier,
        ForeignKey("model_registry_entries.id", ondelete="CASCADE"),
    ),
    Column(
        "to_entry_id",
        identifier,
        ForeignKey("model_registry_entries.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("action", String(16), nullable=False),
    Column("actor", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "model_role ~ '^[a-z][a-z0-9_]{0,63}$'",
        name="ck_model_deployment_events_role_format",
    ),
    CheckConstraint(
        "action IN ('promote', 'rollback')",
        name="ck_model_deployment_events_action",
    ),
    CheckConstraint(
        "from_entry_id IS NULL OR from_entry_id <> to_entry_id",
        name="ck_model_deployment_events_distinct_entries",
    ),
    CheckConstraint(
        "btrim(actor) <> ''",
        name="ck_model_deployment_events_actor_not_blank",
    ),
)

Index(
    "ix_model_deployment_events_role_created",
    model_deployment_events.c.model_role,
    model_deployment_events.c.created_at,
)

review_decisions = Table(
    "review_decisions",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_versions.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("annotation_id", identifier, nullable=False),
    Column("annotation_revision", Integer, nullable=False),
    Column("decision", String(16), nullable=False),
    Column("risk_reasons", json_document, nullable=False),
    Column("reviewer", String(128), nullable=False),
    Column("note", String(500)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    ForeignKeyConstraint(
        ["annotation_id", "annotation_revision"],
        ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
        name="fk_review_decisions_annotation_revision",
        ondelete="RESTRICT",
    ),
    CheckConstraint(
        "annotation_revision > 0",
        name="ck_review_decisions_revision_positive",
    ),
    CheckConstraint(
        "decision IN ('approved', 'flagged')",
        name="ck_review_decisions_decision",
    ),
    CheckConstraint(
        "btrim(reviewer) <> ''",
        name="ck_review_decisions_reviewer_not_blank",
    ),
    CheckConstraint(
        "note IS NULL OR btrim(note) <> ''",
        name="ck_review_decisions_note_not_blank",
    ),
)

Index(
    "ix_review_decisions_version_annotation",
    review_decisions.c.dataset_version_id,
    review_decisions.c.annotation_id,
    review_decisions.c.annotation_revision,
    review_decisions.c.created_at,
)

dataset_review_signoffs = Table(
    "dataset_review_signoffs",
    metadata,
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("signed_by", String(128), nullable=False),
    Column("signed_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("reviewed_annotation_count", Integer, nullable=False),
    Column("risk_item_count", Integer, nullable=False),
    CheckConstraint(
        "btrim(signed_by) <> ''",
        name="ck_dataset_review_signoffs_actor_not_blank",
    ),
    CheckConstraint(
        "reviewed_annotation_count >= 0 AND risk_item_count >= 0",
        name="ck_dataset_review_signoffs_counts",
    ),
)

jobs = Table(
    "jobs",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("job_type", String(64), nullable=False),
    Column("idempotency_key", String(200), nullable=False),
    Column("payload", json_document, nullable=False),
    Column(
        "dataset_version_id",
        identifier,
        ForeignKey("dataset_snapshots.dataset_version_id", ondelete="RESTRICT"),
    ),
    Column("state", String(16), nullable=False, server_default=text("'queued'")),
    Column("progress_current", Integer, nullable=False, server_default=text("0")),
    Column("progress_total", Integer),
    Column("attempt_count", Integer, nullable=False, server_default=text("0")),
    Column("max_attempts", Integer, nullable=False, server_default=text("3")),
    Column("available_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("claimed_by", String(128)),
    Column("lease_expires_at", DateTime(timezone=True)),
    Column(
        "cancellation_requested",
        Boolean,
        nullable=False,
        server_default=text("false"),
    ),
    Column("result", json_document),
    Column("error_code", String(64)),
    Column("error_summary", String(500)),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("updated_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("started_at", DateTime(timezone=True)),
    Column("completed_at", DateTime(timezone=True)),
    CheckConstraint(
        "job_type ~ '^[a-z][a-z0-9_]{0,63}$'",
        name="ck_jobs_type_format",
    ),
    CheckConstraint(
        "btrim(idempotency_key) <> ''",
        name="ck_jobs_idempotency_key_not_blank",
    ),
    CheckConstraint(
        "state IN ('queued', 'running', 'succeeded', 'failed', 'cancelled')",
        name="ck_jobs_state",
    ),
    CheckConstraint(
        "progress_current >= 0 AND "
        "(progress_total IS NULL OR "
        "(progress_total > 0 AND progress_current <= progress_total))",
        name="ck_jobs_progress",
    ),
    CheckConstraint(
        "attempt_count >= 0 AND max_attempts BETWEEN 1 AND 20",
        name="ck_jobs_attempts",
    ),
    CheckConstraint(
        "(state = 'running' AND claimed_by IS NOT NULL "
        "AND lease_expires_at IS NOT NULL) "
        "OR (state <> 'running' AND claimed_by IS NULL "
        "AND lease_expires_at IS NULL)",
        name="ck_jobs_claim",
    ),
    UniqueConstraint(
        "job_type",
        "idempotency_key",
        name="uq_jobs_type_idempotency",
    ),
)

Index("ix_jobs_claimable", jobs.c.state, jobs.c.available_at, jobs.c.created_at)

job_attempts = Table(
    "job_attempts",
    metadata,
    Column(
        "job_id",
        identifier,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("attempt", Integer, primary_key=True),
    Column("worker_id", String(128), nullable=False),
    Column("state", String(16), nullable=False, server_default=text("'running'")),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("finished_at", DateTime(timezone=True)),
    Column("error_code", String(64)),
    Column("error_summary", String(500)),
    CheckConstraint("attempt > 0", name="ck_job_attempts_attempt_positive"),
    CheckConstraint(
        "state IN ('running', 'retry', 'succeeded', 'failed', "
        "'cancelled', 'abandoned')",
        name="ck_job_attempts_state",
    ),
)

worker_heartbeats = Table(
    "worker_heartbeats",
    metadata,
    Column("worker_id", String(128), primary_key=True),
    Column("state", String(16), nullable=False),
    Column(
        "current_job_id",
        identifier,
        ForeignKey("jobs.id", ondelete="SET NULL"),
    ),
    Column("started_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("heartbeat_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "worker_id ~ '^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$'",
        name="ck_worker_heartbeats_id",
    ),
    CheckConstraint(
        "state IN ('idle', 'running', 'stopping')",
        name="ck_worker_heartbeats_state",
    ),
)

Index("ix_worker_heartbeats_time", worker_heartbeats.c.heartbeat_at)

embeddings = Table(
    "embeddings",
    metadata,
    Column("id", identifier, primary_key=True),
    Column("purpose", String(16), nullable=False),
    Column("subject_type", String(24), nullable=False),
    Column("annotation_id", identifier),
    Column("annotation_revision", Integer),
    Column("reference_image_id", identifier),
    Column("target_fingerprint", String(128), nullable=False),
    Column("model_id", String(128), nullable=False),
    Column("model_version", String(128), nullable=False),
    Column("artifact_sha256", String(64), nullable=False),
    Column("configuration", json_document, nullable=False),
    Column("dimension", Integer, nullable=False),
    Column("embedding", VECTOR(), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "purpose IN ('recognition', 'propagation')",
        name="ck_embeddings_purpose",
    ),
    CheckConstraint(
        "subject_type IN ('annotation', 'sku_reference')",
        name="ck_embeddings_subject_type",
    ),
    CheckConstraint(
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
    CheckConstraint(
        "dimension BETWEEN 1 AND 4096 AND vector_dims(embedding) = dimension",
        name="ck_embeddings_dimension",
    ),
    CheckConstraint(
        "target_fingerprint ~ '^sha256:[0-9a-f]{64}$'",
        name="ck_embeddings_target_fingerprint",
    ),
    CheckConstraint(
        "artifact_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_embeddings_artifact_sha256",
    ),
    ForeignKeyConstraint(
        ["annotation_id", "annotation_revision"],
        ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
        ondelete="CASCADE",
    ),
    ForeignKeyConstraint(
        ["reference_image_id"],
        ["sku_reference_images.id"],
        ondelete="CASCADE",
    ),
)

Index(
    "uq_embeddings_annotation_version",
    embeddings.c.purpose,
    embeddings.c.annotation_id,
    embeddings.c.annotation_revision,
    embeddings.c.target_fingerprint,
    embeddings.c.model_id,
    embeddings.c.model_version,
    embeddings.c.artifact_sha256,
    unique=True,
    postgresql_where=embeddings.c.subject_type == "annotation",
)
Index(
    "uq_embeddings_reference_version",
    embeddings.c.purpose,
    embeddings.c.reference_image_id,
    embeddings.c.target_fingerprint,
    embeddings.c.model_id,
    embeddings.c.model_version,
    embeddings.c.artifact_sha256,
    unique=True,
    postgresql_where=embeddings.c.subject_type == "sku_reference",
)
Index(
    "ix_embeddings_recognition_gallery",
    embeddings.c.purpose,
    embeddings.c.model_id,
    embeddings.c.model_version,
    embeddings.c.artifact_sha256,
    postgresql_where=embeddings.c.subject_type == "sku_reference",
)

sku_hard_pairs = Table(
    "sku_hard_pairs",
    metadata,
    Column(
        "first_sku_id",
        identifier,
        ForeignKey("skus.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "second_sku_id",
        identifier,
        ForeignKey("skus.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column("reason", String(255), nullable=False),
    Column(
        "created_at",
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    ),
    CheckConstraint(
        "first_sku_id <> second_sku_id",
        name="ck_sku_hard_pairs_distinct",
    ),
    CheckConstraint("btrim(reason) <> ''", name="ck_sku_hard_pairs_reason_not_blank"),
)

propagation_suggestion_sets = Table(
    "propagation_suggestion_sets",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("seed_annotation_id", identifier, nullable=False),
    Column("seed_annotation_revision", Integer, nullable=False),
    Column("seed_sku_id", identifier, ForeignKey("skus.id", ondelete="RESTRICT"), nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'open'")),
    Column("index_namespace", String(128), nullable=False),
    Column("index_version", String(128), nullable=False),
    Column("model_id", String(128), nullable=False),
    Column("model_version", String(128), nullable=False),
    Column("artifact_sha256", String(64), nullable=False),
    Column("quality_evidence", String(128), nullable=False),
    Column("created_by", String(128), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("completed_by", String(128)),
    Column("completed_at", DateTime(timezone=True)),
    Column("selected_count", Integer),
    Column("skipped_count", Integer),
    ForeignKeyConstraint(
        ["seed_annotation_id", "seed_annotation_revision"],
        ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "status IN ('open', 'completed')",
        name="ck_propagation_sets_status",
    ),
    CheckConstraint(
        "(status = 'open' AND completed_by IS NULL AND completed_at IS NULL "
        "AND selected_count IS NULL AND skipped_count IS NULL) "
        "OR (status = 'completed' AND completed_by IS NOT NULL "
        "AND completed_at IS NOT NULL AND selected_count >= 0 AND skipped_count >= 0)",
        name="ck_propagation_sets_completion",
    ),
    CheckConstraint(
        "artifact_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_propagation_sets_artifact_sha256",
    ),
)

propagation_suggestions = Table(
    "propagation_suggestions",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column(
        "suggestion_set_id",
        identifier,
        ForeignKey("propagation_suggestion_sets.id", ondelete="CASCADE"),
        nullable=False,
    ),
    Column("candidate_annotation_id", identifier, nullable=False),
    Column("candidate_annotation_revision", Integer, nullable=False),
    Column("score", Float, nullable=False),
    Column("status", String(16), nullable=False, server_default=text("'suggested'")),
    Column("requires_individual_review", Boolean, nullable=False),
    Column("risk_reason", String(64)),
    Column("decided_by", String(128)),
    Column("decided_at", DateTime(timezone=True)),
    ForeignKeyConstraint(
        ["candidate_annotation_id", "candidate_annotation_revision"],
        ["annotation_revisions.annotation_id", "annotation_revisions.revision"],
        ondelete="CASCADE",
    ),
    CheckConstraint(
        "score >= -1 AND score <= 1 AND score <> 'NaN'::double precision",
        name="ck_propagation_suggestions_score",
    ),
    CheckConstraint(
        "status IN ('suggested', 'confirmed', 'skipped')",
        name="ck_propagation_suggestions_status",
    ),
    CheckConstraint(
        "(requires_individual_review AND risk_reason IS NOT NULL) "
        "OR (NOT requires_individual_review AND risk_reason IS NULL)",
        name="ck_propagation_suggestions_risk",
    ),
    CheckConstraint(
        "(status = 'suggested' AND decided_by IS NULL AND decided_at IS NULL) "
        "OR (status IN ('confirmed', 'skipped') "
        "AND decided_by IS NOT NULL AND decided_at IS NOT NULL)",
        name="ck_propagation_suggestions_decision",
    ),
    UniqueConstraint(
        "suggestion_set_id",
        "candidate_annotation_id",
        name="uq_propagation_suggestions_set_candidate",
    ),
)

Index(
    "ix_propagation_suggestions_set_status",
    propagation_suggestions.c.suggestion_set_id,
    propagation_suggestions.c.status,
)

auth_sessions = Table(
    "auth_sessions",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("username", String(64), nullable=False),
    Column("role", String(16), nullable=False),
    Column("token_sha256", String(64), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    Column("expires_at", DateTime(timezone=True), nullable=False),
    Column("revoked_at", DateTime(timezone=True)),
    CheckConstraint(
        "username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
        name="ck_auth_sessions_username_format",
    ),
    CheckConstraint(
        "role IN ('owner', 'annotator', 'reviewer')",
        name="ck_auth_sessions_role",
    ),
    CheckConstraint(
        "token_sha256 ~ '^[0-9a-f]{64}$'",
        name="ck_auth_sessions_token_sha256",
    ),
    CheckConstraint(
        "expires_at > created_at",
        name="ck_auth_sessions_expiry",
    ),
    UniqueConstraint("token_sha256", name="uq_auth_sessions_token_sha256"),
)

Index("ix_auth_sessions_active", auth_sessions.c.token_sha256, auth_sessions.c.expires_at)

auth_events = Table(
    "auth_events",
    metadata,
    Column("id", identifier, primary_key=True, server_default=text("gen_random_uuid()")),
    Column("username", String(64)),
    Column("role", String(16)),
    Column("action", String(32), nullable=False),
    Column("request_path", String(512), nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False, server_default=func.now()),
    CheckConstraint(
        "username IS NULL OR username ~ '^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$'",
        name="ck_auth_events_username_format",
    ),
    CheckConstraint(
        "role IS NULL OR role IN ('owner', 'annotator', 'reviewer')",
        name="ck_auth_events_role",
    ),
    CheckConstraint(
        "action IN ('login_success', 'login_failure', 'logout', 'access_denied')",
        name="ck_auth_events_action",
    ),
    CheckConstraint("btrim(request_path) <> ''", name="ck_auth_events_path_not_blank"),
)

Index("ix_auth_events_created", auth_events.c.created_at)
