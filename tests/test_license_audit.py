from __future__ import annotations

import json
from email.message import Message
from importlib.metadata import PackageMetadata
from pathlib import Path
from typing import cast

import pytest

from benchmark_tool.license_audit import (
    LicenseAuditError,
    audit_npm_lock,
    audit_shipped_artifacts,
    effective_license,
)


def test_effective_license_prefers_an_osi_classifier() -> None:
    metadata = Message()
    metadata["Name"] = "example"
    metadata["License"] = "UNKNOWN"
    metadata["Classifier"] = "License :: OSI Approved :: MIT License"

    assert effective_license(cast(PackageMetadata, metadata)) == (
        "License :: OSI Approved :: MIT License",
        "osi_classifier",
    )


def test_effective_license_rejects_missing_metadata() -> None:
    metadata = Message()
    metadata["Name"] = "unlicensed"

    with pytest.raises(LicenseAuditError, match="no approved license"):
        effective_license(cast(PackageMetadata, metadata))


def test_npm_lock_requires_an_approved_license(tmp_path: Path) -> None:
    lock_path = tmp_path / "package-lock.json"
    lock_path.write_text(
        json.dumps(
            {
                "packages": {
                    "": {"name": "app", "version": "1.0.0"},
                    "node_modules/example": {
                        "version": "2.0.0",
                        "license": "MIT",
                    },
                }
            }
        ),
        encoding="utf-8",
    )

    assert audit_npm_lock(lock_path) == {
        "package_count": 1,
        "licenses": ["MIT"],
    }

    lock = json.loads(lock_path.read_text(encoding="utf-8"))
    lock["packages"]["node_modules/example"]["license"] = "UNKNOWN"
    lock_path.write_text(json.dumps(lock), encoding="utf-8")
    with pytest.raises(LicenseAuditError, match="outside policy"):
        audit_npm_lock(lock_path)


def test_shipped_artifact_audit_rejects_model_weights() -> None:
    assert audit_shipped_artifacts(["README.md", "frontend/public/logo.png"])[
        "model_or_dataset_artifacts"
    ] == 0
    with pytest.raises(LicenseAuditError, match="cannot be shipped"):
        audit_shipped_artifacts(["models/checkpoint.safetensors"])
