from __future__ import annotations

import json
from pathlib import Path

import pytest

from shelfsight_api.recovery import (
    BackupValidationError,
    create_backup_manifest,
    restore_media,
    validate_backup_bundle,
)


def _backup_bundle(tmp_path: Path) -> Path:
    bundle = tmp_path / "backup"
    media = bundle / "media"
    media.mkdir(parents=True)
    (bundle / "database.dump").write_bytes(b"postgres-custom-dump")
    (media / "canonical").mkdir()
    (media / "canonical" / "shelf.jpg").write_bytes(b"canonical-image")
    (media / "original.jpg").write_bytes(b"original-image")
    return bundle


def test_backup_manifest_validates_and_restores_media(tmp_path: Path) -> None:
    bundle = _backup_bundle(tmp_path)

    manifest = create_backup_manifest(bundle)
    summary = validate_backup_bundle(bundle)
    target = tmp_path / "restored-media"
    restored = restore_media(bundle, target)

    assert manifest["schema_version"] == "cvsight-backup/v1"
    assert summary == restored
    assert summary["media_files"] == 2
    assert (target / "canonical" / "shelf.jpg").read_bytes() == b"canonical-image"
    assert (target / "original.jpg").read_bytes() == b"original-image"


def test_backup_validation_rejects_tampering_and_unlisted_files(tmp_path: Path) -> None:
    bundle = _backup_bundle(tmp_path)
    create_backup_manifest(bundle)
    (bundle / "media" / "original.jpg").write_bytes(b"changed")

    with pytest.raises(BackupValidationError, match="checksum"):
        validate_backup_bundle(bundle)

    bundle = _backup_bundle(tmp_path / "second")
    create_backup_manifest(bundle)
    (bundle / "media" / "unexpected.jpg").write_bytes(b"extra")
    with pytest.raises(BackupValidationError, match="do not match"):
        validate_backup_bundle(bundle)


def test_backup_validation_rejects_unsafe_paths_and_existing_target(tmp_path: Path) -> None:
    bundle = _backup_bundle(tmp_path)
    create_backup_manifest(bundle)
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["media"]["files"][0]["path"] = "../outside.jpg"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(BackupValidationError, match="path"):
        validate_backup_bundle(bundle)

    bundle = _backup_bundle(tmp_path / "second")
    create_backup_manifest(bundle)
    target = tmp_path / "existing"
    target.mkdir()
    with pytest.raises(BackupValidationError, match="already exists"):
        restore_media(bundle, target)
    with pytest.raises(BackupValidationError, match="inside the backup"):
        restore_media(bundle, bundle / "restored-media")
