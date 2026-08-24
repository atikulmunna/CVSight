from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import uuid4

BACKUP_SCHEMA = "cvsight-backup/v1"
DATABASE_DUMP_NAME = "database.dump"
MANIFEST_NAME = "manifest.json"
MEDIA_DIRECTORY_NAME = "media"
MAX_MANIFEST_BYTES = 16 * 1024 * 1024


class BackupValidationError(ValueError):
    """Raised when a backup bundle is incomplete or unsafe."""


def create_backup_manifest(bundle_directory: Path) -> dict[str, Any]:
    bundle = bundle_directory.resolve()
    manifest_path = bundle / MANIFEST_NAME
    if manifest_path.exists():
        raise BackupValidationError("backup manifest already exists")
    database_dump = bundle / DATABASE_DUMP_NAME
    media_directory = bundle / MEDIA_DIRECTORY_NAME
    if not database_dump.is_file() or database_dump.stat().st_size == 0:
        raise BackupValidationError("database dump is missing or empty")
    if not media_directory.is_dir():
        raise BackupValidationError("media directory is missing")

    media_files = [_file_record(bundle, path) for path in _regular_files(media_directory)]
    manifest = {
        "schema_version": BACKUP_SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "database": _file_record(bundle, database_dump),
        "media": {
            "directory": MEDIA_DIRECTORY_NAME,
            "files": media_files,
        },
    }
    _write_json_once(manifest_path, manifest)
    validate_backup_bundle(bundle)
    return manifest


def validate_backup_bundle(bundle_directory: Path) -> dict[str, Any]:
    bundle = bundle_directory.resolve()
    manifest = _read_manifest(bundle / MANIFEST_NAME)
    if manifest.get("schema_version") != BACKUP_SCHEMA:
        raise BackupValidationError("backup schema is incompatible")
    if not isinstance(manifest.get("created_at"), str):
        raise BackupValidationError("backup timestamp is invalid")

    database = _record(manifest.get("database"), "database")
    if database["path"] != DATABASE_DUMP_NAME:
        raise BackupValidationError("database dump path is invalid")
    media = manifest.get("media")
    if not isinstance(media, dict) or media.get("directory") != MEDIA_DIRECTORY_NAME:
        raise BackupValidationError("media manifest is invalid")
    media_values = media.get("files")
    if not isinstance(media_values, list):
        raise BackupValidationError("media file manifest is invalid")
    media_records = [_record(value, "media file") for value in media_values]
    declared = [database, *media_records]
    declared_paths = [record["path"] for record in declared]
    if len(declared_paths) != len(set(declared_paths)):
        raise BackupValidationError("backup contains duplicate file records")
    if any(
        path != DATABASE_DUMP_NAME and not path.startswith(f"{MEDIA_DIRECTORY_NAME}/")
        for path in declared_paths
    ):
        raise BackupValidationError("backup contains a file outside its declared boundary")

    actual_paths = {
        path.relative_to(bundle).as_posix()
        for path in _regular_files(bundle)
        if path.name != MANIFEST_NAME
    }
    if actual_paths != set(declared_paths):
        raise BackupValidationError("backup files do not match the manifest")
    for record in declared:
        path = _resolve_declared_path(bundle, record["path"])
        if path.stat().st_size != record["size"] or _sha256_file(path) != record["sha256"]:
            raise BackupValidationError("backup file checksum does not match")
    return {
        "schema_version": BACKUP_SCHEMA,
        "database_bytes": database["size"],
        "media_files": len(media_records),
        "media_bytes": sum(record["size"] for record in media_records),
    }


def restore_media(bundle_directory: Path, target_directory: Path) -> dict[str, Any]:
    bundle = bundle_directory.resolve()
    summary = validate_backup_bundle(bundle)
    target = target_directory.resolve()
    if target.exists():
        raise BackupValidationError("media restore target already exists")
    if target == bundle or target.is_relative_to(bundle):
        raise BackupValidationError("media restore target cannot be inside the backup")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.restore-{uuid4().hex}")
    try:
        shutil.copytree(
            bundle / MEDIA_DIRECTORY_NAME,
            temporary,
            copy_function=shutil.copy2,
        )
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)
    return summary


def _regular_files(directory: Path) -> list[Path]:
    if directory.is_symlink():
        raise BackupValidationError("backup directories cannot be symbolic links")
    files: list[Path] = []
    for path in directory.rglob("*"):
        if path.is_symlink():
            raise BackupValidationError("backup files cannot be symbolic links")
        if path.is_file():
            files.append(path)
    return sorted(files, key=lambda path: path.as_posix())


def _file_record(bundle: Path, path: Path) -> dict[str, Any]:
    return {
        "path": path.relative_to(bundle).as_posix(),
        "size": path.stat().st_size,
        "sha256": _sha256_file(path),
    }


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json_once(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as output:
            json.dump(value, output, indent=2, sort_keys=True, allow_nan=False)
            output.write("\n")
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        content = path.read_bytes()
    except OSError as error:
        raise BackupValidationError("backup manifest is missing") from error
    if not content or len(content) > MAX_MANIFEST_BYTES:
        raise BackupValidationError("backup manifest size is invalid")
    try:
        value = json.loads(content)
    except json.JSONDecodeError as error:
        raise BackupValidationError("backup manifest is invalid JSON") from error
    if not isinstance(value, dict):
        raise BackupValidationError("backup manifest must be an object")
    return value


def _record(value: object, label: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"path", "size", "sha256"}:
        raise BackupValidationError(f"{label} record is invalid")
    path = value["path"]
    size = value["size"]
    checksum = value["sha256"]
    if not isinstance(path, str) or _safe_relative_path(path) is None:
        raise BackupValidationError(f"{label} path is invalid")
    if not isinstance(size, int) or isinstance(size, bool) or size < 0:
        raise BackupValidationError(f"{label} size is invalid")
    if (
        not isinstance(checksum, str)
        or len(checksum) != 64
        or any(character not in "0123456789abcdef" for character in checksum)
    ):
        raise BackupValidationError(f"{label} checksum is invalid")
    return {"path": path, "size": size, "sha256": checksum}


def _safe_relative_path(value: str) -> PurePosixPath | None:
    if not value or "\\" in value or ":" in value:
        return None
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path


def _resolve_declared_path(bundle: Path, value: str) -> Path:
    relative = _safe_relative_path(value)
    if relative is None:
        raise BackupValidationError("backup path is unsafe")
    resolved = bundle.joinpath(*relative.parts).resolve()
    if not resolved.is_relative_to(bundle):
        raise BackupValidationError("backup path leaves the bundle")
    return resolved


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate CVSight backup media")
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create-manifest")
    create.add_argument("--bundle", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--bundle", type=Path, required=True)
    restore = commands.add_parser("restore-media")
    restore.add_argument("--bundle", type=Path, required=True)
    restore.add_argument("--target", type=Path, required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    try:
        if arguments.command == "create-manifest":
            result = create_backup_manifest(arguments.bundle)
            summary = {
                "schema_version": result["schema_version"],
                "media_files": len(result["media"]["files"]),
            }
        elif arguments.command == "verify":
            summary = validate_backup_bundle(arguments.bundle)
        else:
            summary = restore_media(arguments.bundle, arguments.target)
    except (BackupValidationError, OSError) as error:
        print(f"backup operation failed: {error}")
        return 1
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
