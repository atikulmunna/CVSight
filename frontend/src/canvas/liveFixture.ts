import { parseCanvasFixture, type CanvasFixture, type OverlayState } from "./model";

type Fetcher = typeof fetch;

const UNAVAILABLE_SKU_NAME = "SKU name unavailable";

export class LiveFixtureError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("live image workspace failed to load");
  }
}

export async function loadLiveFixture(
  imageId: string,
  fetcher: Fetcher = fetch,
): Promise<CanvasFixture> {
  if (!isUuid(imageId)) {
    throw new LiveFixtureError(400, "invalid_image_id");
  }
  const encodedId = encodeURIComponent(imageId);
  const [imageResponse, annotationsResponse] = await Promise.all([
    fetcher(`/api/images/${encodedId}`, {
      headers: { Accept: "application/json" },
    }),
    fetcher(`/api/images/${encodedId}/annotations`, {
      headers: { Accept: "application/json" },
    }),
  ]);
  const imageBody = await responseBody(imageResponse);
  const annotationsBody = await responseBody(annotationsResponse);
  if (!imageResponse.ok) {
    throw requestError(imageResponse.status, imageBody);
  }
  if (!annotationsResponse.ok) {
    throw requestError(annotationsResponse.status, annotationsBody);
  }

  const image = record(imageBody);
  const annotationList = record(annotationsBody).annotations;
  if (!Array.isArray(annotationList)) {
    throw new LiveFixtureError(502, "invalid_response");
  }
  const loadedImageId = requiredString(image.id);
  if (loadedImageId !== imageId) {
    throw new LiveFixtureError(502, "invalid_response");
  }
  const skuNames = await loadSkuNames(annotationList, fetcher);

  try {
    return parseCanvasFixture({
      name: requiredString(image.original_filename),
      image: {
        width: image.width,
        height: image.height,
      },
      images: [
        {
          id: loadedImageId,
          url: image.canonical_url,
          x: 0,
          y: 0,
          width: image.width,
          height: image.height,
        },
      ],
      boxes: annotationList.map((value) => liveAnnotation(value, loadedImageId, skuNames)),
    });
  } catch (error) {
    if (error instanceof LiveFixtureError) {
      throw error;
    }
    throw new LiveFixtureError(502, "invalid_response");
  }
}

// Annotations carry SKU ids only, so the names a reviewer checks against are looked up
// once per distinct SKU in the image. A failed lookup is shown, not hidden.
async function loadSkuNames(
  annotations: unknown[],
  fetcher: Fetcher,
): Promise<Map<string, string>> {
  const ids = new Set<string>();
  for (const value of annotations) {
    const skuId = value && typeof value === "object" ? (value as Record<string, unknown>).sku_id : null;
    if (typeof skuId === "string" && isUuid(skuId)) {
      ids.add(skuId);
    }
  }
  const entries = await Promise.all(
    [...ids].map(async (skuId) => {
      const response = await fetcher(`/api/skus/${encodeURIComponent(skuId)}`, {
        headers: { Accept: "application/json" },
      });
      const body = await responseBody(response);
      const name =
        response.ok && body && typeof body === "object"
          ? (body as Record<string, unknown>).name
          : null;
      return [skuId, typeof name === "string" && name ? name : UNAVAILABLE_SKU_NAME] as const;
    }),
  );
  return new Map(entries);
}

function liveAnnotation(
  value: unknown,
  imageId: string,
  skuNames: Map<string, string>,
): Record<string, unknown> {
  const annotation = record(value);
  if (requiredString(annotation.image_id) !== imageId) {
    throw new LiveFixtureError(502, "invalid_response");
  }
  const skuId = annotation.sku_id;
  return {
    id: requiredString(annotation.id),
    server_id: annotation.id,
    image_id: annotation.image_id,
    revision: annotation.revision,
    x: annotation.x,
    y: annotation.y,
    width: annotation.width,
    height: annotation.height,
    kind: annotation.class_type,
    state: overlayState(
      annotation.lifecycle_state,
      annotation.review_state,
      annotation.source,
    ),
    lifecycle_state: annotation.lifecycle_state,
    review_state: annotation.review_state,
    sku:
      typeof skuId === "string"
        ? (skuNames.get(skuId) ?? UNAVAILABLE_SKU_NAME)
        : "Unknown SKU",
    sku_id: skuId,
    confidence: annotation.confidence,
    occluded: annotation.occluded,
    truncated: annotation.truncated,
    shelf_row: annotation.shelf_row,
    image_index: 0,
  };
}

function overlayState(
  lifecycle: unknown,
  review: unknown,
  source: unknown,
): OverlayState {
  if (review === "flagged") {
    return "flagged";
  }
  if (lifecycle === "verified" && review === "accepted") {
    return "verified";
  }
  if (source === "propagated") {
    return "propagated";
  }
  return "unverified";
}

export function isUuid(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(
    value,
  );
}

function requestError(status: number, value: unknown): LiveFixtureError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new LiveFixtureError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new LiveFixtureError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new LiveFixtureError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new LiveFixtureError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || value === "") {
    throw new LiveFixtureError(502, "invalid_response");
  }
  return value;
}

function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}
