import { errorCode } from "./annotationApi";
import type { BoxGeometry, ScenePoint } from "./editing";

type Fetcher = typeof fetch;

export class SmartBoxError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("smart box request failed");
  }
}

// The box SAM finds around the object at a point, in the photo's own pixels.
export async function requestSmartBox(
  imageId: string,
  point: ScenePoint,
  fetcher: Fetcher = fetch,
): Promise<BoxGeometry> {
  const response = await fetcher(`/api/images/${encodeURIComponent(imageId)}/smart-box`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ x: point.x, y: point.y }),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new SmartBoxError(response.status, errorCode(body));
  }
  return smartBoxGeometry(body);
}

// What a reviewer should do about a failed smart box, by the API's error code.
export function smartBoxMessage(error: unknown): string {
  const code = error instanceof SmartBoxError ? error.code : "request_failed";
  const messages: Record<string, string> = {
    smart_box_unavailable: "Smart box is off: no SAM service is configured.",
    smart_box_misconfigured: "Smart box is off: its service settings are invalid.",
    segmenter_unavailable: "Smart box could not reach the SAM service. Is the tunnel open?",
    segmenter_rejected: "The SAM service refused the configured token.",
    no_object: "No object found at that point. Draw the box instead.",
    image_not_found: "Smart box works on saved photos only.",
  };
  return messages[code] ?? "Smart box failed. Try again or draw the box.";
}

function smartBoxGeometry(value: unknown): BoxGeometry {
  if (!value || typeof value !== "object") {
    throw new SmartBoxError(502, "invalid_response");
  }
  const body = value as Record<string, unknown>;
  const geometry = {
    x: body.x,
    y: body.y,
    width: body.width,
    height: body.height,
  };
  const valid = Object.values(geometry).every(
    (number) => typeof number === "number" && Number.isFinite(number) && number >= 0,
  );
  if (!valid || (geometry.width as number) <= 0 || (geometry.height as number) <= 0) {
    throw new SmartBoxError(502, "invalid_response");
  }
  return geometry as BoxGeometry;
}
