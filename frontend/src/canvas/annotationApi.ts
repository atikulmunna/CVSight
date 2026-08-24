import type { EditIntent } from "./editing";
import type {
  AnnotationBox,
  AnnotationClass,
  AnnotationLifecycle,
  AnnotationReviewState,
  OverlayState,
} from "./model";

type Fetcher = typeof fetch;

export class AnnotationSaveError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("annotation save failed");
  }
}

export async function saveAnnotation(
  box: AnnotationBox,
  intent: EditIntent,
  fetcher: Fetcher = fetch,
): Promise<AnnotationBox> {
  const request = saveRequest(box, intent);
  if (!request) {
    return box;
  }
  const response = await fetcher(request.url, {
    method: request.method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify(request.body),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new AnnotationSaveError(response.status, errorCode(body));
  }
  return mapAnnotationResponse(body, box);
}

export async function fetchAnnotation(
  localBox: AnnotationBox,
  fetcher: Fetcher = fetch,
): Promise<AnnotationBox> {
  if (!localBox.serverId) {
    throw new AnnotationSaveError(400, "missing_server_id");
  }
  const response = await fetcher(
    `/api/annotations/${encodeURIComponent(localBox.serverId)}`,
    { headers: { Accept: "application/json" } },
  );
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new AnnotationSaveError(response.status, errorCode(body));
  }
  return mapAnnotationResponse(body, localBox);
}

function saveRequest(
  box: AnnotationBox,
  intent: EditIntent,
): { url: string; method: "POST" | "PUT"; body: Record<string, unknown> } | null {
  if (intent === "create") {
    if (!box.imageId) {
      return null;
    }
    return {
      url: `/api/images/${encodeURIComponent(box.imageId)}/annotations`,
      method: "POST",
      body: {
        ...annotationFields(box),
        lifecycle_state: "proposed",
      },
    };
  }
  if (!box.serverId || box.revision === null) {
    return null;
  }
  if (intent === "accept" || intent === "reject") {
    return {
      url: `/api/annotations/${encodeURIComponent(box.serverId)}/${intent}`,
      method: "POST",
      body: { expected_revision: box.revision },
    };
  }
  if (intent === "assign") {
    if (!box.skuId) {
      return null;
    }
    return {
      url: `/api/annotations/${encodeURIComponent(box.serverId)}/assign-sku`,
      method: "POST",
      body: {
        expected_revision: box.revision,
        sku_id: box.skuId,
      },
    };
  }
  return {
    url: `/api/annotations/${encodeURIComponent(box.serverId)}`,
    method: "PUT",
    body: {
      ...annotationFields(box),
      expected_revision: box.revision,
    },
  };
}

function annotationFields(box: AnnotationBox): Record<string, unknown> {
  return {
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
    class_type: box.classType,
    sku_id: box.skuId,
    review_state: box.reviewState,
    confidence: box.confidence,
    occluded: box.occluded,
    truncated: box.truncated,
    shelf_row: box.shelfRow,
  };
}

export function mapAnnotationResponse(
  value: unknown,
  previous: AnnotationBox,
): AnnotationBox {
  const body = record(value);
  const lifecycleState = enumValue(
    body.lifecycle_state,
    ["proposed", "verified", "rejected"] as const,
    "lifecycle state",
  );
  const reviewState = enumValue(
    body.review_state,
    ["unreviewed", "accepted", "flagged"] as const,
    "review state",
  );
  const source = enumValue(
    body.source,
    ["model", "human", "propagated", "imported"] as const,
    "source",
  );
  const classType = enumValue(
    body.class_type,
    ["product", "gap", "shelf_label"] as const,
    "class",
  );
  return {
    id: previous.id,
    serverId: stringValue(body.id, "annotation id"),
    imageId: stringValue(body.image_id, "image id"),
    revision: positiveInteger(body.revision, "revision"),
    x: nonNegativeNumber(body.x, "x"),
    y: nonNegativeNumber(body.y, "y"),
    width: positiveNumber(body.width, "width"),
    height: positiveNumber(body.height, "height"),
    classType: classType as AnnotationClass,
    state: overlayState(
      lifecycleState as AnnotationLifecycle,
      reviewState as AnnotationReviewState,
      source,
    ),
    lifecycleState: lifecycleState as AnnotationLifecycle,
    reviewState: reviewState as AnnotationReviewState,
    sku: previous.sku,
    skuId: nullableString(body.sku_id),
    confidence: nullableProbability(body.confidence),
    occluded: body.occluded === true,
    truncated: body.truncated === true,
    shelfRow: nullableInteger(body.shelf_row),
    imageIndex: previous.imageIndex,
  };
}

function overlayState(
  lifecycle: AnnotationLifecycle,
  review: AnnotationReviewState,
  source: string,
): OverlayState {
  if (review === "flagged") {
    return "flagged";
  }
  if (lifecycle === "verified" && review === "accepted") {
    return "verified";
  }
  return source === "propagated" ? "propagated" : "unverified";
}

function errorCode(value: unknown): string {
  if (!value || typeof value !== "object") {
    return "request_failed";
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object") {
    return "request_failed";
  }
  const code = (detail as Record<string, unknown>).code;
  return typeof code === "string" ? code : "request_failed";
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new AnnotationSaveError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function enumValue<T extends string>(
  value: unknown,
  allowed: readonly T[],
  label: string,
): T {
  if (typeof value !== "string" || !allowed.includes(value as T)) {
    throw new AnnotationSaveError(502, `invalid_${label.replace(" ", "_")}`);
  }
  return value as T;
}

function stringValue(value: unknown, label: string): string {
  if (typeof value !== "string" || value === "") {
    throw new AnnotationSaveError(502, `invalid_${label.replace(" ", "_")}`);
  }
  return value;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : stringValue(value, "nullable string");
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new AnnotationSaveError(502, `invalid_${label}`);
  }
  return value;
}

function positiveNumber(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (number <= 0) {
    throw new AnnotationSaveError(502, `invalid_${label}`);
  }
  return number;
}

function nonNegativeNumber(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (number < 0) {
    throw new AnnotationSaveError(502, `invalid_${label}`);
  }
  return number;
}

function positiveInteger(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (!Number.isInteger(number) || number < 1) {
    throw new AnnotationSaveError(502, `invalid_${label}`);
  }
  return number;
}

function nullableProbability(value: unknown): number | null {
  if (value === null) {
    return null;
  }
  const number = finiteNumber(value, "confidence");
  if (number < 0 || number > 1) {
    throw new AnnotationSaveError(502, "invalid_confidence");
  }
  return number;
}

function nullableInteger(value: unknown): number | null {
  if (value === null) {
    return null;
  }
  const number = finiteNumber(value, "nullable_integer");
  if (!Number.isInteger(number) || number < 0) {
    throw new AnnotationSaveError(502, "invalid_nullable_integer");
  }
  return number;
}
