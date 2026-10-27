import { errorCode } from "./annotationApi";
import type { BoxGeometry, ScenePoint } from "./editing";
import { isPolygon, type PolygonPoint } from "./model";

type Fetcher = typeof fetch;

export type SmartTool = "Smart box" | "Smart polygon";

// A smart polygon click: on the object, or on something its outline must leave out.
export type SmartClick = ScenePoint & { include: boolean };

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
  const body = await askSam(imageId, "smart-box", { x: point.x, y: point.y }, fetcher);
  return smartBoxGeometry(body);
}

// The outline SAM finds for the object the clicks describe, in the photo's own pixels.
export async function requestSmartPolygon(
  imageId: string,
  clicks: SmartClick[],
  fetcher: Fetcher = fetch,
): Promise<PolygonPoint[]> {
  const body = await askSam(
    imageId,
    "smart-polygon",
    { points: clicks.map(({ x, y, include }) => ({ x, y, include })) },
    fetcher,
  );
  const polygon = body && typeof body === "object" ? (body as Record<string, unknown>).polygon : null;
  if (!isPolygon(polygon)) {
    throw new SmartBoxError(502, "invalid_response");
  }
  return polygon;
}

async function askSam(
  imageId: string,
  route: "smart-box" | "smart-polygon",
  prompt: Record<string, unknown>,
  fetcher: Fetcher,
): Promise<unknown> {
  const response = await fetcher(`/api/images/${encodeURIComponent(imageId)}/${route}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(prompt),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new SmartBoxError(response.status, errorCode(body));
  }
  return body;
}

// What a reviewer should do about a failed smart tool, by the API's error code.
export function smartBoxMessage(error: unknown, tool: SmartTool = "Smart box"): string {
  const code = error instanceof SmartBoxError ? error.code : "request_failed";
  const drawInstead = tool === "Smart box" ? " Draw the box instead." : "";
  const messages: Record<string, string> = {
    smart_box_unavailable: `${tool} is off: no SAM service is configured.`,
    smart_box_misconfigured: `${tool} is off: its service settings are invalid.`,
    segmenter_unavailable: `${tool} could not reach the SAM service. Is the tunnel open?`,
    segmenter_rejected: "The SAM service refused the configured token.",
    segmenter_outdated: `The SAM service is out of date. Update it to use ${tool}.`,
    no_object: `No object found at that point.${drawInstead}`,
    image_not_found: `${tool} works on saved photos only.`,
  };
  return (
    messages[code] ??
    `${tool} failed. Try again${tool === "Smart box" ? " or draw the box" : ""}.`
  );
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
