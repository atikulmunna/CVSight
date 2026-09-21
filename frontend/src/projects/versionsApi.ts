export type ProjectVersionStatus = "working" | "released" | "frozen";
export type ProjectExportType = "detection" | "recognition";

export type VersionReviewSignoff = {
  signedBy: string;
  signedAt: string;
  reviewedAnnotationCount: number;
  riskItemCount: number;
};

export type ProjectVersion = {
  id: string;
  datasetId: string;
  parentVersionId: string | null;
  createdAt: string;
  snapshotAt: string | null;
  status: ProjectVersionStatus;
  imageCount: number;
  reviewSignoff: VersionReviewSignoff | null;
  exportTypes: ProjectExportType[];
};

export type WorkingVersionReview = {
  totalRiskItems: number;
  unresolvedCount: number;
  resolvedCount: number;
};

export class ProjectVersionsApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("project versions request failed");
  }
}

export async function loadProjectVersions(
  projectId: string,
  fetcher: typeof fetch = fetch,
): Promise<ProjectVersion[]> {
  const validatedProjectId = uuid(projectId);
  const response = await fetcher(`/api/datasets/${encodeURIComponent(validatedProjectId)}/versions`, {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  if (!Array.isArray(body)) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  const versions = body.map(parseVersion);
  const versionIds = new Set(versions.map((version) => version.id));
  if (
    versions.some((version) => version.datasetId !== validatedProjectId) ||
    versionIds.size !== versions.length ||
    versions.filter((version) => version.status === "working").length > 1
  ) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return versions;
}

export async function loadWorkingVersionReview(
  versionId: string,
  fetcher: typeof fetch = fetch,
): Promise<WorkingVersionReview> {
  const response = await fetcher(
    `/api/dataset-versions/${encodeURIComponent(uuid(versionId))}/review-queue`,
    { headers: { Accept: "application/json" } },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  const queue = record(body);
  const review = {
    totalRiskItems: nonNegativeInteger(queue.total_risk_items),
    unresolvedCount: nonNegativeInteger(queue.unresolved_count),
    resolvedCount: nonNegativeInteger(queue.resolved_count),
  };
  if (review.unresolvedCount + review.resolvedCount !== review.totalRiskItems) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return review;
}

export async function createWorkingVersion(
  projectId: string,
  fetcher: typeof fetch = fetch,
): Promise<string> {
  const response = await fetcher(`/api/datasets/${encodeURIComponent(uuid(projectId))}/versions`, {
    method: "POST",
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return uuid(record(body).id);
}

export function versionExportUrl(versionId: string, exportType: ProjectExportType): string {
  return `/api/dataset-versions/${encodeURIComponent(uuid(versionId))}/exports/${exportType}`;
}

function parseVersion(value: unknown): ProjectVersion {
  const version = record(value);
  const status = version.status;
  if (!isVersionStatus(status)) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  const snapshotAt = nullableDate(version.snapshot_at);
  const signoff = version.review_signoff === null
    ? null
    : parseSignoff(version.review_signoff);
  if (
    (status === "working" && (snapshotAt !== null || signoff !== null)) ||
    (status === "released" && (snapshotAt === null || signoff === null)) ||
    (status === "frozen" && (snapshotAt === null || signoff !== null))
  ) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  if (!Array.isArray(version.export_types)) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  const exportTypes = version.export_types.map((item) => {
    if (item !== "detection" && item !== "recognition") {
      throw new ProjectVersionsApiError(502, "invalid_response");
    }
    return item;
  });
  if (new Set(exportTypes).size !== exportTypes.length) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return {
    id: uuid(version.id),
    datasetId: uuid(version.dataset_id),
    parentVersionId: version.parent_version_id === null ? null : uuid(version.parent_version_id),
    createdAt: date(version.created_at),
    snapshotAt,
    status,
    imageCount: nonNegativeInteger(version.image_count),
    reviewSignoff: signoff,
    exportTypes,
  };
}

function parseSignoff(value: unknown): VersionReviewSignoff {
  const signoff = record(value);
  return {
    signedBy: requiredString(signoff.signed_by),
    signedAt: date(signoff.signed_at),
    reviewedAnnotationCount: nonNegativeInteger(signoff.reviewed_annotation_count),
    riskItemCount: nonNegativeInteger(signoff.risk_item_count),
  };
}

function isVersionStatus(value: unknown): value is ProjectVersionStatus {
  return value === "working" || value === "released" || value === "frozen";
}

function requestError(status: number, value: unknown): ProjectVersionsApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new ProjectVersionsApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new ProjectVersionsApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new ProjectVersionsApiError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return value;
}

function nonNegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return value;
}

function date(value: unknown): string {
  const parsed = requiredString(value);
  if (Number.isNaN(Date.parse(parsed))) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return parsed;
}

function nullableDate(value: unknown): string | null {
  return value === null ? null : date(value);
}

function uuid(value: unknown): string {
  const parsed = requiredString(value);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(parsed)) {
    throw new ProjectVersionsApiError(502, "invalid_response");
  }
  return parsed;
}

function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}
