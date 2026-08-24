export type MetricStatus = "complete" | "partial" | "provisional" | "unsupported";

export type ShareMetric = {
  status: MetricStatus;
  isFinal: boolean;
  reason: string | null;
  shares: Record<string, number>;
};

export type AnalyticsImage = {
  imageId: string;
  imageName: string;
  imageStatus: string;
  activeProductFacings: number;
  acceptedProductFacings: number;
  countShare: ShareMetric;
  imageAreaShare: ShareMetric;
  realogramStatus: MetricStatus;
  acceptedGaps: number;
};

export type AnalyticsReport = {
  datasetVersionId: string;
  formulaVersion: string;
  resultSha256: string;
  generatedAt: string;
  snapshotSha256: string;
  groupLabels: Record<string, string>;
  summary: {
    images: number;
    reviewedImages: number;
    acceptedProductFacings: number;
    acceptedGaps: number;
    finalCountShareImages: number;
  };
  planogramStatus: MetricStatus;
  planogramReason: string | null;
  images: AnalyticsImage[];
};

type Fetcher = typeof fetch;

export class AnalyticsApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("analytics request failed");
  }
}

export async function loadAnalytics(
  datasetVersionId: string,
  fetcher: Fetcher = fetch,
): Promise<AnalyticsReport> {
  requireUuid(datasetVersionId);
  const response = await fetcher(
    `/api/dataset-versions/${encodeURIComponent(datasetVersionId)}/analytics`,
    { headers: { Accept: "application/json" } },
  );
  const body: unknown = await response.json();
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseAnalyticsReport(body);
}

export function analyticsExportUrl(
  datasetVersionId: string,
  format: "csv" | "json",
): string {
  requireUuid(datasetVersionId);
  return (
    `/api/dataset-versions/${encodeURIComponent(datasetVersionId)}` +
    `/analytics/export?format=${format}`
  );
}

export function parseAnalyticsReport(value: unknown): AnalyticsReport {
  const data = record(value);
  if (data.schema_version !== "shelfsight-analytics-response/v1") {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  const snapshot = record(data.snapshot);
  const summary = record(data.summary);
  const planogram = record(data.planogram);
  if (!Array.isArray(data.images)) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return {
    datasetVersionId: uuid(data.dataset_version_id),
    formulaVersion: requiredString(data.formula_version),
    resultSha256: sha256(data.result_sha256),
    generatedAt: requiredString(data.generated_at),
    snapshotSha256: sha256(snapshot.content_sha256),
    groupLabels: stringRecord(data.group_labels),
    summary: {
      images: nonNegativeInteger(summary.images),
      reviewedImages: nonNegativeInteger(summary.reviewed_images),
      acceptedProductFacings: nonNegativeInteger(summary.accepted_product_facings),
      acceptedGaps: nonNegativeInteger(summary.accepted_gaps),
      finalCountShareImages: nonNegativeInteger(summary.final_count_share_images),
    },
    planogramStatus: metricStatus(planogram.status),
    planogramReason: optionalString(planogram.reason),
    images: data.images.map(parseImage),
  };
}

function parseImage(value: unknown): AnalyticsImage {
  const data = record(value);
  const realogram = record(data.realogram);
  const gaps = record(data.gaps);
  return {
    imageId: uuid(data.image_id),
    imageName: requiredString(data.image_name),
    imageStatus: requiredString(data.image_status),
    activeProductFacings: nonNegativeInteger(data.active_product_facings),
    acceptedProductFacings: nonNegativeInteger(data.accepted_product_facings),
    countShare: parseShareMetric(data.count_share),
    imageAreaShare: parseShareMetric(data.image_area_share),
    realogramStatus: metricStatus(realogram.status),
    acceptedGaps: nonNegativeInteger(gaps.accepted_count),
  };
}

function parseShareMetric(value: unknown): ShareMetric {
  const data = record(value);
  return {
    status: metricStatus(data.status),
    isFinal: requiredBoolean(data.is_final),
    reason: optionalString(data.reason),
    shares: data.shares === undefined ? {} : numericShareRecord(data.shares),
  };
}

function numericShareRecord(value: unknown): Record<string, number> {
  const values = record(value);
  return Object.fromEntries(
    Object.entries(values).map(([key, item]) => {
      if (typeof item !== "number" || !Number.isFinite(item) || item < 0 || item > 1) {
        throw new AnalyticsApiError(502, "invalid_response");
      }
      return [key, item];
    }),
  );
}

function stringRecord(value: unknown): Record<string, string> {
  const values = record(value);
  return Object.fromEntries(
    Object.entries(values).map(([key, item]) => [key, requiredString(item)]),
  );
}

function metricStatus(value: unknown): MetricStatus {
  if (!["complete", "partial", "provisional", "unsupported"].includes(String(value))) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return value as MetricStatus;
}

function requestError(status: number, value: unknown): AnalyticsApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new AnalyticsApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new AnalyticsApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new AnalyticsApiError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return value;
}

function optionalString(value: unknown): string | null {
  if (value === null || value === undefined) {
    return null;
  }
  return requiredString(value);
}

function requiredBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return value;
}

function nonNegativeInteger(value: unknown): number {
  if (!Number.isInteger(value) || Number(value) < 0) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return Number(value);
}

function uuid(value: unknown): string {
  const candidate = requiredString(value);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(candidate)) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return candidate;
}

function sha256(value: unknown): string {
  const candidate = requiredString(value);
  if (!/^[0-9a-f]{64}$/.test(candidate)) {
    throw new AnalyticsApiError(502, "invalid_response");
  }
  return candidate;
}

function requireUuid(value: string): void {
  uuid(value);
}
