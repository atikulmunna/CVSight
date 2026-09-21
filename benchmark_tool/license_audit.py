from __future__ import annotations

import argparse
import importlib.metadata
import json
import re
import subprocess
import tomllib
from datetime import UTC, datetime
from importlib.metadata import PackageMetadata
from pathlib import Path
from typing import Any

REPORT_SCHEMA = "cvsight-license-audit/v1"
APPROVED_NPM_LICENSES = {
    "0BSD",
    "Apache-2.0",
    "BlueOak-1.0.0",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC-BY-4.0",
    "CC0-1.0",
    "ISC",
    "MIT",
    "MIT-0",
    "MPL-2.0",
    "OFL-1.1",
}
APPROVED_LICENSE_MARKERS = (
    "APACHE",
    "BSD",
    "CMU",
    "ISC",
    "LESSER GENERAL PUBLIC LICENSE",
    "LGPL",
    "MIT",
    "MOZILLA PUBLIC LICENSE",
    "MPL",
    "PSF",
    "PYTHON SOFTWARE FOUNDATION",
)
FORBIDDEN_ARTIFACT_SUFFIXES = {
    ".7z",
    ".ckpt",
    ".onnx",
    ".pt",
    ".pth",
    ".rar",
    ".safetensors",
    ".tar",
    ".zip",
}
NAME_SEPARATOR = re.compile(r"[-_.]+")


class LicenseAuditError(ValueError):
    """Raised when a dependency or shipped artifact violates release policy."""


def effective_license(metadata: PackageMetadata) -> tuple[str, str]:
    classifiers = [
        value
        for value in metadata.get_all("Classifier", [])
        if value.startswith("License :: OSI Approved :: ")
    ]
    if classifiers:
        return "; ".join(classifiers), "osi_classifier"
    declared = (
        metadata.get("License-Expression") or metadata.get("License") or ""
    ).strip()
    if declared and declared.upper() != "UNKNOWN":
        return declared.splitlines()[0].strip(), "metadata"
    raise LicenseAuditError(
        f"{metadata.get('Name', 'unnamed package')} has no approved license metadata"
    )


def license_is_approved(value: str) -> bool:
    upper = value.upper()
    return any(marker in upper for marker in APPROVED_LICENSE_MARKERS)


def audit_python_environment(
    locked_names: set[str] | None,
) -> list[dict[str, str]]:
    installed = {
        _canonical_name(distribution.metadata["Name"]): distribution
        for distribution in importlib.metadata.distributions()
        if distribution.metadata.get("Name")
    }
    names = sorted(locked_names if locked_names is not None else set(installed))
    records: list[dict[str, str]] = []
    for name in names:
        if name == "shelfsight":
            continue
        distribution = installed.get(name)
        if distribution is None:
            raise LicenseAuditError(f"locked Python package is not installed: {name}")
        license_value, source = effective_license(distribution.metadata)
        if not license_is_approved(license_value):
            raise LicenseAuditError(
                f"Python package license is outside policy: {name} {license_value}"
            )
        records.append(
            {
                "name": name,
                "version": distribution.version,
                "license": license_value,
                "license_source": source,
            }
        )
    return records


def locked_python_names(path: Path) -> set[str]:
    with path.open("rb") as source:
        lock = tomllib.load(source)
    packages = lock.get("package")
    if not isinstance(packages, list):
        raise LicenseAuditError("uv.lock has no package list")
    return {
        _canonical_name(str(package["name"]))
        for package in packages
        if isinstance(package, dict) and package.get("name")
    }


def audit_npm_lock(path: Path) -> dict[str, Any]:
    lock = json.loads(path.read_text(encoding="utf-8"))
    packages = lock.get("packages")
    if not isinstance(packages, dict):
        raise LicenseAuditError("npm lock has no package map")
    records: list[dict[str, str]] = []
    for package_path, package in sorted(packages.items()):
        if not package_path or not isinstance(package, dict) or "version" not in package:
            continue
        license_value = package.get("license")
        if license_value not in APPROVED_NPM_LICENSES:
            raise LicenseAuditError(
                f"npm package license is outside policy: {package_path} {license_value}"
            )
        records.append(
            {
                "path": package_path,
                "version": str(package["version"]),
                "license": str(license_value),
            }
        )
    return {
        "package_count": len(records),
        "licenses": sorted({record["license"] for record in records}),
    }


def audit_shipped_artifacts(paths: list[str]) -> dict[str, Any]:
    unsafe = sorted(
        path
        for path in paths
        if Path(path).suffix.lower() in FORBIDDEN_ARTIFACT_SUFFIXES
    )
    if unsafe:
        raise LicenseAuditError(
            "model, dataset, or archive artifacts cannot be shipped: " + ", ".join(unsafe)
        )
    return {
        "file_count": len(paths),
        "model_or_dataset_artifacts": 0,
    }


def repository_paths() -> list[str]:
    result = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
        text=True,
    )
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def build_report(
    project_root: Path,
    *,
    all_installed: bool,
    python_only: bool,
    environment_label: str,
) -> dict[str, Any]:
    names = None if all_installed else locked_python_names(project_root / "uv.lock")
    python_packages = audit_python_environment(names)
    report: dict[str, Any] = {
        "schema_version": REPORT_SCHEMA,
        "generated_at": datetime.now(UTC).isoformat(),
        "environment": environment_label,
        "python": {
            "package_count": len(python_packages),
            "packages": python_packages,
        },
        "passed": True,
    }
    if not python_only:
        projection_requirement = (
            project_root / "projection-requirements.txt"
        ).read_text(encoding="utf-8").strip()
        if projection_requirement != "fiftyone==1.19.0":
            raise LicenseAuditError("FiftyOne must remain pinned to the approved 1.19.0 release")
        report["npm"] = audit_npm_lock(project_root / "frontend" / "package-lock.json")
        report["shipped_files"] = audit_shipped_artifacts(repository_paths())
        report["external_component_posture"] = {
            "fiftyone": "Apache-2.0, optional operator projection",
            "pgvector": "PostgreSQL, database extension and base image",
            "rfdetr_nano": "Apache-2.0, operator-supplied checkpoint",
            "openai_clip": "MIT, exploratory operator-supplied checkpoint",
            "sam3": "custom license, operator-supplied and not approved for redistribution",
            "qpds_seg": "CC-BY-4.0, local benchmark dataset with attribution",
        }
    return report


def _canonical_name(value: str) -> str:
    return NAME_SEPARATOR.sub("-", value).lower()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Audit CVSight release licenses")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--all-installed", action="store_true")
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--environment-label", default="main")
    return parser


def main(argv: list[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    project_root = Path.cwd().resolve()
    try:
        report = build_report(
            project_root,
            all_installed=arguments.all_installed,
            python_only=arguments.python_only,
            environment_label=arguments.environment_label,
        )
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(
            json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    except (LicenseAuditError, OSError, subprocess.SubprocessError, json.JSONDecodeError) as error:
        print(f"license audit failed: {error}")
        return 1
    print(
        f"license audit passed: environment={arguments.environment_label} "
        f"python_packages={report['python']['package_count']}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
