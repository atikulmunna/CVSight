from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from io import StringIO
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import Connection, select

from benchmark_tool.analytics import reconstruct_realogram, summarize_share_of_shelf
from shelfsight_api.data_model import get_dataset_snapshot
from shelfsight_api.models import dataset_versions

ANALYTICS_SCHEMA_VERSION = "shelfsight-analytics-response/v1"
FORMULA_VERSION = "shelfsight-retail-analytics/v1"
CSV_COLUMNS = (
    "dataset_version_id",
    "snapshot_sha256",
    "formula_version",
    "result_sha256",
    "generated_at",
    "image_id",
    "image_name",
    "metric",
    "group_id",
    "group_label",
    "value",
    "unit",
    "status",
    "is_final",
    "confidence_status",
    "reason",
)


class AnalyticsVersionNotFoundError(ValueError):
    """Raised when analytics reference a missing dataset version."""


class AnalyticsVersionNotFrozenError(ValueError):
    """Raised when analytics reference a mutable dataset version."""


def build_snapshot_analytics(
    connection: Connection,
    dataset_version_id: UUID,
    *,
    generated_at: datetime | None = None,
) -> dict[str, Any]:
    version = connection.execute(
        select(dataset_versions.c.snapshot_at).where(
            dataset_versions.c.id == dataset_version_id
        )
    ).one_or_none()
    if version is None:
        raise AnalyticsVersionNotFoundError("dataset version does not exist")
    if version.snapshot_at is None:
        raise AnalyticsVersionNotFrozenError("dataset version must be snapshotted")

    snapshot = get_dataset_snapshot(connection, dataset_version_id)
    images = snapshot["images"]
    annotations = snapshot["annotations"]
    skus = snapshot["skus"]
    sku_by_id = {str(sku["sku_id"]): sku for sku in skus}
    group_labels = _group_labels(skus)
    annotations_by_image: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for annotation in annotations:
        annotations_by_image[str(annotation["image_id"])].append(annotation)

    image_results = [
        _image_analytics(
            image,
            annotations_by_image[str(image["image_id"])],
            sku_by_id,
        )
        for image in images
    ]
    summary = {
        "images": len(image_results),
        "reviewed_images": sum(image["image_status"] == "reviewed" for image in image_results),
        "active_product_facings": sum(
            image["active_product_facings"] for image in image_results
        ),
        "accepted_product_facings": sum(
            image["accepted_product_facings"] for image in image_results
        ),
        "accepted_gaps": sum(image["gaps"]["accepted_count"] for image in image_results),
        "final_count_share_images": sum(
            image["count_share"]["is_final"] for image in image_results
        ),
    }
    core = {
        "schema_version": ANALYTICS_SCHEMA_VERSION,
        "dataset_id": str(snapshot["dataset_id"]),
        "dataset_version_id": str(snapshot["dataset_version_id"]),
        "snapshot": {
            "schema_version": str(snapshot["schema_version"]),
            "content_sha256": str(snapshot["content_sha256"]),
            "snapshot_at": _isoformat(snapshot["snapshot_at"]),
        },
        "formula_version": FORMULA_VERSION,
        "model": {
            "basis": "verified_snapshot_annotations",
            "required": False,
            "artifact_id": None,
            "reason": "accepted human-corrected annotations have no authoritative model binding",
        },
        "group_by": "sku",
        "group_labels": group_labels,
        "summary": summary,
        "planogram": {
            "status": "unsupported",
            "is_final": False,
            "reason": "no_planogram_reference",
            "compliance_rate": None,
            "confidence": _confidence("unsupported"),
        },
        "images": image_results,
    }
    result_sha256 = hashlib.sha256(_canonical_json(core)).hexdigest()
    timestamp = generated_at or datetime.now(UTC)
    if timestamp.tzinfo is None or timestamp.utcoffset() is None:
        raise ValueError("generated_at must be timezone-aware")
    return {
        **core,
        "result_sha256": result_sha256,
        "generated_at": _isoformat(timestamp),
    }


def build_analytics_json(report: Mapping[str, Any]) -> bytes:
    return (json.dumps(report, indent=2, allow_nan=False) + "\n").encode("utf-8")


def build_analytics_csv(report: Mapping[str, Any]) -> bytes:
    stream = StringIO(newline="")
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS, lineterminator="\r\n")
    writer.writeheader()
    for row in _analytics_csv_rows(report):
        writer.writerow({column: _spreadsheet_cell(row.get(column, "")) for column in CSV_COLUMNS})
    return b"\xef\xbb\xbf" + stream.getvalue().encode("utf-8")


def _image_analytics(
    image: Mapping[str, Any],
    annotations: Sequence[Mapping[str, Any]],
    sku_by_id: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    image_id = str(image["image_id"])
    image_complete = image["status"] == "reviewed"
    active = [row for row in annotations if row["lifecycle_state"] != "rejected"]
    products = [row for row in active if row["class_type"] == "product"]
    gaps = [row for row in active if row["class_type"] == "gap"]
    accepted_products = [row for row in products if _accepted(row)]
    view = _capture_view(image.get("capture_metadata"))

    if products:
        share = summarize_share_of_shelf(
            [_share_facing(row, sku_by_id) for row in products],
            observation_complete=image_complete,
            view=view,
        )
        count_share = _metric_result(share["count_share"])
        area_share = _metric_result(share["image_area_share"])
        realogram = _realogram_result(
            products,
            sku_by_id,
            observation_complete=image_complete,
            view=view,
        )
    else:
        count_share = _unsupported_metric("count_share_of_shelf", "no_product_facings")
        area_share = _unsupported_metric("image_area_share", "no_product_facings")
        realogram = _unsupported_metric("realogram", "no_product_facings")

    gap_result = _gap_result(gaps, image_complete)
    return {
        "image_id": image_id,
        "image_name": str(image["original_filename"]),
        "image_status": str(image["status"]),
        "capture_view": view,
        "active_product_facings": len(products),
        "accepted_product_facings": len(accepted_products),
        "count_share": count_share,
        "image_area_share": area_share,
        "realogram": realogram,
        "gaps": gap_result,
    }


def _share_facing(
    row: Mapping[str, Any], sku_by_id: Mapping[str, Mapping[str, Any]]
) -> dict[str, Any]:
    group_id = _resolved_group_id(row.get("sku_id"), sku_by_id)
    return {
        "annotation_id": str(row["annotation_id"]),
        "bbox": [row["x"], row["y"], row["width"], row["height"]],
        "group_id": group_id,
        "identity_state": (
            "unknown"
            if group_id is None
            else "conflicted"
            if row["review_state"] == "flagged"
            else "known"
        ),
        "review_state": row["review_state"],
        "class_type": "product",
    }


def _realogram_result(
    products: Sequence[Mapping[str, Any]],
    sku_by_id: Mapping[str, Mapping[str, Any]],
    *,
    observation_complete: bool,
    view: Literal["frontal", "mild_oblique", "severe_oblique"] | None,
) -> dict[str, Any]:
    assigned = sum(row["shelf_row"] is not None for row in products)
    if assigned not in {0, len(products)}:
        return _unsupported_metric("realogram", "incomplete_shelf_row_assignments")
    facings = [
        {
            "annotation_id": str(row["annotation_id"]),
            "bbox": [row["x"], row["y"], row["width"], row["height"]],
            "shelf_row_id": row["shelf_row"],
            "sku_id": _resolved_group_id(row.get("sku_id"), sku_by_id),
            "review_state": row["review_state"],
            "class_type": "product",
        }
        for row in products
    ]
    result = reconstruct_realogram(
        facings,
        observation_complete=observation_complete,
        view=view if view is not None else "frontal",
    )
    if view is None and result["status"] == "complete":
        result = {**result, "status": "provisional", "reason": "capture_view_not_declared"}
    return _metric_result({"metric": "realogram", **result})


def _gap_result(gaps: Sequence[Mapping[str, Any]], image_complete: bool) -> dict[str, Any]:
    accepted = [row for row in gaps if _accepted(row)]
    unresolved = len(gaps) - len(accepted)
    if not image_complete:
        status = "partial"
    elif unresolved:
        status = "provisional"
    else:
        status = "complete"
    rows: Counter[str] = Counter(
        "unassigned" if row["shelf_row"] is None else str(row["shelf_row"])
        for row in accepted
    )
    result = {
        "metric": "reviewed_gap_count",
        "status": status,
        "reason": None,
        "accepted_count": len(accepted),
        "unresolved_count": unresolved,
        "counts_by_shelf_row": dict(sorted(rows.items())),
        "visible_gap_fraction": {
            "status": "unsupported",
            "is_final": False,
            "reason": "reviewed_shelf_row_geometry_unavailable",
            "value": None,
            "confidence": _confidence("unsupported"),
        },
    }
    return _metric_result(result)


def _resolved_group_id(
    sku_id: object, sku_by_id: Mapping[str, Mapping[str, Any]]
) -> str | None:
    if sku_id is None:
        return None
    sku = sku_by_id.get(str(sku_id))
    if sku is None or sku.get("is_unknown") is True:
        return None
    if sku.get("status") == "merged":
        target = sku.get("merged_into_id")
        return str(target) if target is not None and str(target) in sku_by_id else None
    return str(sku_id)


def _group_labels(skus: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    labels = {"unknown": "Unknown"}
    sku_by_id = {str(sku["sku_id"]): sku for sku in skus}
    for sku in skus:
        group_id = _resolved_group_id(sku["sku_id"], sku_by_id)
        if group_id is not None:
            target = sku_by_id[group_id]
            labels[group_id] = str(target["name"])
    return dict(sorted(labels.items()))


def _capture_view(metadata: object) -> Literal["frontal", "mild_oblique", "severe_oblique"] | None:
    if not isinstance(metadata, Mapping):
        return None
    provided = metadata.get("provided")
    if not isinstance(provided, Mapping):
        return None
    value = provided.get("view")
    if value == "frontal":
        return "frontal"
    if value == "mild_oblique":
        return "mild_oblique"
    if value == "severe_oblique":
        return "severe_oblique"
    return None


def _accepted(row: Mapping[str, Any]) -> bool:
    return bool(
        row["lifecycle_state"] == "verified"
        and row["review_state"] == "accepted"
    )


def _metric_result(metric: Mapping[str, Any]) -> dict[str, Any]:
    status = str(metric["status"])
    return {
        **metric,
        "is_final": status == "complete",
        "confidence": _confidence(status),
    }


def _unsupported_metric(metric: str, reason: str) -> dict[str, Any]:
    return _metric_result(
        {
            "metric": metric,
            "status": "unsupported",
            "reason": reason,
        }
    )


def _confidence(status: str) -> dict[str, Any]:
    confidence_status = (
        "confirmed"
        if status == "complete"
        else "provisional"
        if status in {"partial", "provisional"}
        else "unknown"
    )
    return {
        "status": confidence_status,
        "probability": None,
        "calibrated": False,
    }


def _analytics_csv_rows(report: Mapping[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    common = {
        "dataset_version_id": report["dataset_version_id"],
        "snapshot_sha256": report["snapshot"]["content_sha256"],
        "formula_version": report["formula_version"],
        "result_sha256": report["result_sha256"],
        "generated_at": report["generated_at"],
    }
    labels = report["group_labels"]
    for image in report["images"]:
        image_common = {
            **common,
            "image_id": image["image_id"],
            "image_name": image["image_name"],
        }
        rows.extend(
            _share_csv_rows(image_common, image["count_share"], labels, "fraction")
        )
        rows.extend(
            _share_csv_rows(
                image_common,
                image["image_area_share"],
                labels,
                "fraction",
            )
        )
        rows.append(
            _single_metric_row(
                image_common,
                image["realogram"],
                len(image["realogram"].get("rows", [])),
                "rows",
            )
        )
        rows.append(
            _single_metric_row(
                image_common,
                image["gaps"],
                image["gaps"]["accepted_count"],
                "gaps",
            )
        )
    rows.append(
        _single_metric_row(
            common,
            report["planogram"],
            report["planogram"]["compliance_rate"],
            "fraction",
            metric="planogram_compliance",
        )
    )
    return rows


def _share_csv_rows(
    common: Mapping[str, Any],
    metric: Mapping[str, Any],
    labels: Mapping[str, str],
    unit: str,
) -> list[dict[str, Any]]:
    shares = metric.get("shares")
    if not isinstance(shares, Mapping):
        return [_single_metric_row(common, metric, None, unit)]
    return [
        _single_metric_row(
            {
                **common,
                "group_id": group_id,
                "group_label": labels.get(str(group_id), str(group_id)),
            },
            metric,
            value,
            unit,
        )
        for group_id, value in shares.items()
    ]


def _single_metric_row(
    common: Mapping[str, Any],
    metric_result: Mapping[str, Any],
    value: object,
    unit: str,
    *,
    metric: str | None = None,
) -> dict[str, Any]:
    return {
        **common,
        "metric": metric or metric_result.get("metric", "unknown"),
        "value": "" if value is None else value,
        "unit": unit,
        "status": metric_result["status"],
        "is_final": str(bool(metric_result["is_final"])).lower(),
        "confidence_status": metric_result["confidence"]["status"],
        "reason": metric_result.get("reason") or "",
    }


def _spreadsheet_cell(value: object) -> object:
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def _canonical_json(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _isoformat(value: object) -> str:
    if not isinstance(value, datetime):
        raise ValueError("snapshot timestamp is invalid")
    return value.isoformat()
