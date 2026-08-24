export type ReviewRiskCode =
  | "annotation_flagged"
  | "hard_pair_confusion"
  | "consistency_conflict"
  | "low_agreement"
  | "propagated_origin"
  | "unknown_status"
  | "manual_flag";

export type ReviewRiskReason = {
  code: ReviewRiskCode;
  label: string;
  weight: number;
  blocking: true;
};

export type ReviewItem = {
  annotationId: string;
  annotationRevision: number;
  imageId: string;
  imageName: string;
  imageUrl: string;
  riskScore: number;
  riskReasons: ReviewRiskReason[];
  resolved: boolean;
};

export type ReviewSignoff = {
  datasetVersionId: string;
  signedBy: string;
  signedAt: string;
  reviewedAnnotationCount: number;
  riskItemCount: number;
};

export type ReviewQueue = {
  datasetVersionId: string;
  status: "open" | "signed";
  totalRiskItems: number;
  unresolvedCount: number;
  resolvedCount: number;
  items: ReviewItem[];
  signoff: ReviewSignoff | null;
};

type Fetcher = typeof fetch;

export class ReviewApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("review request failed");
  }
}

export async function loadReviewQueue(
  datasetVersionId: string,
  fetcher: Fetcher = fetch,
): Promise<ReviewQueue> {
  requireUuid(datasetVersionId);
  const response = await fetcher(
    `/api/dataset-versions/${encodeURIComponent(datasetVersionId)}/review-queue`,
    { headers: { Accept: "application/json" } },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseQueue(body);
}

export async function submitReviewDecision(
  datasetVersionId: string,
  item: ReviewItem,
  decision: "approve" | "flag",
  fetcher: Fetcher = fetch,
): Promise<void> {
  requireUuid(datasetVersionId);
  requireUuid(item.annotationId);
  const response = await fetcher(
    `/api/dataset-versions/${encodeURIComponent(datasetVersionId)}` +
      `/review-items/${encodeURIComponent(item.annotationId)}/decision`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        expected_revision: item.annotationRevision,
        decision,
        note: null,
      }),
    },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
}

export async function signOffReview(
  datasetVersionId: string,
  fetcher: Fetcher = fetch,
): Promise<ReviewSignoff> {
  requireUuid(datasetVersionId);
  const response = await fetcher(
    `/api/dataset-versions/${encodeURIComponent(datasetVersionId)}/review-signoff`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
    },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseSignoff(body);
}

function parseQueue(value: unknown): ReviewQueue {
  const data = record(value);
  const items = data.items;
  if (!Array.isArray(items)) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return {
    datasetVersionId: uuid(data.dataset_version_id),
    status: queueStatus(data.status),
    totalRiskItems: nonNegativeInteger(data.total_risk_items),
    unresolvedCount: nonNegativeInteger(data.unresolved_count),
    resolvedCount: nonNegativeInteger(data.resolved_count),
    items: items.map(parseItem),
    signoff: data.signoff === null ? null : parseSignoff(data.signoff),
  };
}

function parseItem(value: unknown): ReviewItem {
  const data = record(value);
  if (!Array.isArray(data.risk_reasons)) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return {
    annotationId: uuid(data.annotation_id),
    annotationRevision: positiveInteger(data.annotation_revision),
    imageId: uuid(data.image_id),
    imageName: requiredString(data.image_name),
    imageUrl: safeApiPath(data.image_url),
    riskScore: nonNegativeInteger(data.risk_score),
    riskReasons: data.risk_reasons.map(parseReason),
    resolved: requiredBoolean(data.resolved),
  };
}

function parseReason(value: unknown): ReviewRiskReason {
  const data = record(value);
  const code = requiredString(data.code);
  if (!isRiskCode(code) || data.blocking !== true) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return {
    code,
    label: requiredString(data.label),
    weight: nonNegativeInteger(data.weight),
    blocking: true,
  };
}

function parseSignoff(value: unknown): ReviewSignoff {
  const data = record(value);
  return {
    datasetVersionId: uuid(data.dataset_version_id),
    signedBy: requiredString(data.signed_by),
    signedAt: requiredString(data.signed_at),
    reviewedAnnotationCount: nonNegativeInteger(data.reviewed_annotation_count),
    riskItemCount: nonNegativeInteger(data.risk_item_count),
  };
}

function isRiskCode(value: string): value is ReviewRiskCode {
  return [
    "annotation_flagged",
    "hard_pair_confusion",
    "consistency_conflict",
    "low_agreement",
    "propagated_origin",
    "unknown_status",
    "manual_flag",
  ].includes(value);
}

function requestError(status: number, value: unknown): ReviewApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new ReviewApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new ReviewApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new ReviewApiError(status, typeof code === "string" ? code : "request_failed");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return value;
}

function requiredBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    throw new ReviewApiError(502, "invalid_response");
  }
  return value;
}

function nonNegativeInteger(value: unknown): number {
  if (!Number.isInteger(value) || (value as number) < 0) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return value as number;
}

function positiveInteger(value: unknown): number {
  const parsed = nonNegativeInteger(value);
  if (parsed < 1) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return parsed;
}

function uuid(value: unknown): string {
  const parsed = requiredString(value);
  requireUuid(parsed);
  return parsed;
}

function requireUuid(value: string): void {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new ReviewApiError(400, "invalid_id");
  }
}

function safeApiPath(value: unknown): string {
  const parsed = requiredString(value);
  if (!parsed.startsWith("/api/") || parsed.includes("\\")) {
    throw new ReviewApiError(502, "invalid_response");
  }
  return parsed;
}

function queueStatus(value: unknown): "open" | "signed" {
  if (value !== "open" && value !== "signed") {
    throw new ReviewApiError(502, "invalid_response");
  }
  return value;
}

function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}
