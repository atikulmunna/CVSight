export type AnnotationClass = "product" | "gap" | "shelf_label";
export type OverlayState = "unverified" | "verified" | "propagated" | "flagged";
export type AnnotationLifecycle = "proposed" | "verified" | "rejected";
export type AnnotationReviewState = "unreviewed" | "accepted" | "flagged";

export type AnnotationBox = {
  id: string;
  serverId: string | null;
  imageId: string | null;
  revision: number | null;
  x: number;
  y: number;
  width: number;
  height: number;
  classType: AnnotationClass;
  state: OverlayState;
  lifecycleState: AnnotationLifecycle;
  reviewState: AnnotationReviewState;
  sku: string;
  skuId: string | null;
  confidence: number | null;
  occluded: boolean;
  truncated: boolean;
  shelfRow: number | null;
  imageIndex: number;
};

export type FixtureImage = {
  id: string;
  url: string;
  x: number;
  y: number;
  width: number;
  height: number;
};

export type CanvasFixture = {
  name: string;
  width: number;
  height: number;
  images: FixtureImage[];
  annotations: AnnotationBox[];
};

const MAX_FIXTURE_BOXES = 1_000;
const OVERLAY_STATES = new Set<OverlayState>([
  "unverified",
  "verified",
  "propagated",
  "flagged",
]);
const ANNOTATION_CLASSES = new Set<AnnotationClass>([
  "product",
  "gap",
  "shelf_label",
]);

export function spatialReadingOrder(annotations: AnnotationBox[]): AnnotationBox[] {
  if (annotations.some((box) => box.shelfRow !== null)) {
    return [...annotations].sort((left, right) => {
      const rowDifference =
        (left.shelfRow ?? Number.MAX_SAFE_INTEGER) -
        (right.shelfRow ?? Number.MAX_SAFE_INTEGER);
      return rowDifference || horizontalOrder(left, right);
    });
  }

  const rows: Array<{
    center: number;
    averageHeight: number;
    boxes: AnnotationBox[];
  }> = [];
  const verticalOrder = [...annotations].sort(
    (left, right) => verticalCenter(left) - verticalCenter(right),
  );
  for (const box of verticalOrder) {
    const center = verticalCenter(box);
    const row = rows.find(
      (candidate) =>
        Math.abs(candidate.center - center) <=
        Math.max(16, Math.min(candidate.averageHeight, box.height) * 0.55),
    );
    if (!row) {
      rows.push({ center, averageHeight: box.height, boxes: [box] });
      continue;
    }
    const previousCount = row.boxes.length;
    row.boxes.push(box);
    row.center = (row.center * previousCount + center) / row.boxes.length;
    row.averageHeight =
      (row.averageHeight * previousCount + box.height) / row.boxes.length;
  }

  return rows
    .sort((left, right) => left.center - right.center)
    .flatMap((row) => row.boxes.sort(horizontalOrder));
}

export function overlayCue(box: AnnotationBox): string {
  if (box.classType === "gap") {
    return "hatch";
  }
  if (box.classType === "shelf_label") {
    return "dot";
  }
  const cues: Record<OverlayState, string> = {
    unverified: "dash",
    verified: "opacity",
    propagated: "double",
    flagged: "weight",
  };
  return cues[box.state];
}

export function parseCanvasFixture(value: unknown): CanvasFixture {
  const fixture = requireRecord(value, "fixture");
  const image = requireRecord(fixture.image, "fixture image");
  const width = requirePositiveNumber(image.width, "fixture width");
  const height = requirePositiveNumber(image.height, "fixture height");
  const sourceImages = requireArray(fixture.images, "fixture images");
  const sourceBoxes = requireArray(fixture.boxes, "fixture boxes");
  if (sourceImages.length === 0) {
    throw new Error("fixture must contain at least one image");
  }
  if (sourceBoxes.length > MAX_FIXTURE_BOXES) {
    throw new Error(`fixture must contain at most ${MAX_FIXTURE_BOXES} boxes`);
  }

  const images = sourceImages.map((source, index) =>
    parseFixtureImage(source, index, width, height),
  );
  const identifiers = new Set<string>();
  const annotations = sourceBoxes.map((source, index) => {
    const box = parseAnnotation(source, index, width, height, images.length);
    if (identifiers.has(box.id)) {
      throw new Error(`fixture contains duplicate box id ${box.id}`);
    }
    identifiers.add(box.id);
    return box;
  });

  return {
    name: typeof fixture.name === "string" ? fixture.name : "Local dense fixture",
    width,
    height,
    images,
    annotations,
  };
}

function parseFixtureImage(
  value: unknown,
  index: number,
  fixtureWidth: number,
  fixtureHeight: number,
): FixtureImage {
  const image = requireRecord(value, `image ${index + 1}`);
  const parsed = {
    id: requireString(image.id, `image ${index + 1} id`),
    url: requireSafeImageUrl(image.url, `image ${index + 1} URL`),
    x: requireNonNegativeNumber(image.x, `image ${index + 1} x`),
    y: requireNonNegativeNumber(image.y, `image ${index + 1} y`),
    width: requirePositiveNumber(image.width, `image ${index + 1} width`),
    height: requirePositiveNumber(image.height, `image ${index + 1} height`),
  };
  if (parsed.x + parsed.width > fixtureWidth || parsed.y + parsed.height > fixtureHeight) {
    throw new Error(`image ${index + 1} exceeds fixture bounds`);
  }
  return parsed;
}

function parseAnnotation(
  value: unknown,
  index: number,
  fixtureWidth: number,
  fixtureHeight: number,
  imageCount: number,
): AnnotationBox {
  const source = requireRecord(value, `box ${index + 1}`);
  const classType = source.kind;
  const state = source.state;
  if (typeof classType !== "string" || !ANNOTATION_CLASSES.has(classType as AnnotationClass)) {
    throw new Error(`box ${index + 1} has an invalid class`);
  }
  if (typeof state !== "string" || !OVERLAY_STATES.has(state as OverlayState)) {
    throw new Error(`box ${index + 1} has an invalid state`);
  }
  const x = requireNonNegativeNumber(source.x, `box ${index + 1} x`);
  const y = requireNonNegativeNumber(source.y, `box ${index + 1} y`);
  const width = requirePositiveNumber(source.width, `box ${index + 1} width`);
  const height = requirePositiveNumber(source.height, `box ${index + 1} height`);
  if (x + width > fixtureWidth || y + height > fixtureHeight) {
    throw new Error(`box ${index + 1} exceeds fixture bounds`);
  }
  const imageIndex = source.image_index;
  if (
    typeof imageIndex !== "number" ||
    !Number.isInteger(imageIndex) ||
    imageIndex < 0 ||
    imageIndex >= imageCount
  ) {
    throw new Error(`box ${index + 1} has an invalid image index`);
  }

  return {
    id: requireString(source.id, `box ${index + 1} id`),
    serverId: optionalString(source.server_id),
    imageId: optionalString(source.image_id),
    revision: optionalPositiveInteger(source.revision, `box ${index + 1} revision`),
    x,
    y,
    width,
    height,
    classType: classType as AnnotationClass,
    state: state as OverlayState,
    lifecycleState: parseLifecycle(source.lifecycle_state, state as OverlayState),
    reviewState: parseReviewState(source.review_state, state as OverlayState),
    sku: typeof source.sku === "string" ? source.sku : "Unknown SKU",
    skuId: optionalString(source.sku_id),
    confidence: optionalProbability(source.confidence, `box ${index + 1} confidence`),
    occluded: source.occluded === true,
    truncated: source.truncated === true,
    shelfRow:
      typeof source.shelf_row === "number" && Number.isInteger(source.shelf_row)
        ? source.shelf_row
        : null,
    imageIndex,
  };
}

function parseLifecycle(
  value: unknown,
  state: OverlayState,
): AnnotationLifecycle {
  if (value === "proposed" || value === "verified" || value === "rejected") {
    return value;
  }
  return state === "verified" ? "verified" : "proposed";
}

function parseReviewState(
  value: unknown,
  state: OverlayState,
): AnnotationReviewState {
  if (value === "unreviewed" || value === "accepted" || value === "flagged") {
    return value;
  }
  if (state === "verified") {
    return "accepted";
  }
  return state === "flagged" ? "flagged" : "unreviewed";
}

function requireRecord(value: unknown, label: string): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`${label} must be an object`);
  }
  return value as Record<string, unknown>;
}

function requireArray(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new Error(`${label} must be an array`);
  }
  return value;
}

function requireString(value: unknown, label: string): string {
  if (typeof value !== "string" || value.trim() === "") {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function optionalString(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function optionalPositiveInteger(value: unknown, label: string): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "number" || !Number.isInteger(value) || value < 1) {
    throw new Error(`${label} must be a positive integer`);
  }
  return value;
}

function requirePositiveNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value <= 0) {
    throw new Error(`${label} must be a positive finite number`);
  }
  return value;
}

function requireNonNegativeNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0) {
    throw new Error(`${label} must be a non-negative finite number`);
  }
  return value;
}

function optionalProbability(value: unknown, label: string): number | null {
  if (value === null || value === undefined) {
    return null;
  }
  if (typeof value !== "number" || !Number.isFinite(value) || value < 0 || value > 1) {
    throw new Error(`${label} must be between zero and one`);
  }
  return value;
}

function requireSafeImageUrl(value: unknown, label: string): string {
  const url = requireString(value, label);
  if (!url.startsWith("/") || url.startsWith("//") || url.includes("..")) {
    throw new Error(`${label} must be a same-origin absolute path`);
  }
  return url;
}

function verticalCenter(box: AnnotationBox): number {
  return box.y + box.height / 2;
}

function horizontalOrder(left: AnnotationBox, right: AnnotationBox): number {
  return left.x - right.x || left.id.localeCompare(right.id);
}
