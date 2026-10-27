import type { AnnotationBox, AnnotationClass, FixtureImage, PolygonPoint } from "./model";

export type EditIntent =
  | "accept"
  | "reject"
  | "update"
  | "create"
  | "assign"
  | "reopen"
  | "restore";

export type SceneBounds = {
  width: number;
  height: number;
};

export type ScenePoint = {
  x: number;
  y: number;
};

const MIN_BOX_SIZE = 4;
const DUPLICATE_OFFSET = 8;

let localIdCount = 0;

// An id for a box the server has not saved yet. The time keeps ids unique across
// reloads, which restart the count, so a restored draft never collides with a new box.
export function localBoxId(prefix: string): string {
  localIdCount += 1;
  return `${prefix}-${Date.now()}-${localIdCount}`;
}

export function acceptBox(box: AnnotationBox): AnnotationBox {
  if (
    box.lifecycleState === "rejected" ||
    (box.lifecycleState === "verified" && box.reviewState === "accepted")
  ) {
    return box;
  }
  return {
    ...box,
    state: "verified",
    lifecycleState: "verified",
    reviewState: "accepted",
  };
}

export function reopenBox(box: AnnotationBox): AnnotationBox {
  if (box.lifecycleState !== "verified") {
    return box;
  }
  return {
    ...box,
    state: "unverified",
    lifecycleState: "proposed",
    reviewState: "unreviewed",
  };
}

export function flagBox(box: AnnotationBox): AnnotationBox {
  if (box.lifecycleState === "rejected" || box.reviewState === "flagged") {
    return box;
  }
  return {
    ...box,
    state: "flagged",
    reviewState: "flagged",
  };
}

export function rejectBox(box: AnnotationBox): AnnotationBox {
  return box.lifecycleState === "rejected"
    ? box
    : { ...box, lifecycleState: "rejected" };
}

export function assignSku(
  box: AnnotationBox,
  skuId: string,
  skuName: string,
): AnnotationBox {
  if (
    box.classType !== "product" ||
    box.lifecycleState !== "verified" ||
    box.reviewState !== "accepted"
  ) {
    return box;
  }
  return { ...box, skuId, sku: skuName };
}

export function changeBoxClass(
  box: AnnotationBox,
  classType: AnnotationClass,
): AnnotationBox {
  if (box.lifecycleState === "rejected" || box.classType === classType) {
    return box;
  }
  const sku =
    classType === "gap"
      ? "Visible gap"
      : classType === "shelf_label"
        ? "Shelf label"
        : "Unknown SKU";
  return {
    ...box,
    classType,
    // Only products carry outlines.
    polygon: classType === "product" ? box.polygon : null,
    sku,
    skuId: null,
    confidence: null,
    state: "unverified",
    reviewState: "unreviewed",
  };
}

export function nudgeBox(
  box: AnnotationBox,
  deltaX: number,
  deltaY: number,
  bounds: SceneBounds,
): AnnotationBox {
  const x = clamp(box.x + deltaX, 0, bounds.width - box.width);
  const y = clamp(box.y + deltaY, 0, bounds.height - box.height);
  return x === box.x && y === box.y
    ? box
    : { ...withGeometry(box, { ...box, x, y }), confidence: null };
}

export function resizeBox(
  box: AnnotationBox,
  deltaWidth: number,
  deltaHeight: number,
  bounds: SceneBounds,
): AnnotationBox {
  const width = clamp(
    box.width + deltaWidth,
    MIN_BOX_SIZE,
    bounds.width - box.x,
  );
  const height = clamp(
    box.height + deltaHeight,
    MIN_BOX_SIZE,
    bounds.height - box.y,
  );
  return width === box.width && height === box.height
    ? box
    : { ...withGeometry(box, { ...box, width, height }), confidence: null };
}

export function duplicateBox(
  box: AnnotationBox,
  id: string,
  bounds: SceneBounds,
): AnnotationBox {
  const x = clamp(box.x + DUPLICATE_OFFSET, 0, bounds.width - box.width);
  const y = clamp(box.y + DUPLICATE_OFFSET, 0, bounds.height - box.height);
  return withGeometry(asNewProposal(box, id), { ...box, x, y });
}

// A copied box keeps its place within its photo. On the photo it came from it lands
// offset, like a duplicate, so it does not hide the original.
export function pasteBox(
  box: AnnotationBox,
  id: string,
  sourceImage: FixtureImage | undefined,
  targetImages: readonly FixtureImage[],
): AnnotationBox {
  const imageIndex = Math.min(box.imageIndex, targetImages.length - 1);
  const image = targetImages[imageIndex]!;
  const offset = sourceImage?.id === image.id ? DUPLICATE_OFFSET : 0;
  const width = Math.min(box.width, image.width);
  const height = Math.min(box.height, image.height);
  return withGeometry(
    { ...asNewProposal(box, id), imageId: image.id, imageIndex },
    {
      x: clamp(
        image.x + box.x - (sourceImage?.x ?? 0) + offset,
        image.x,
        image.x + image.width - width,
      ),
      y: clamp(
        image.y + box.y - (sourceImage?.y ?? 0) + offset,
        image.y,
        image.y + image.height - height,
      ),
      width,
      height,
    },
  );
}

// A copy is a new, unsaved proposal: a human verifies it again where it lands.
function asNewProposal(box: AnnotationBox, id: string): AnnotationBox {
  return {
    ...box,
    id,
    serverId: null,
    revision: null,
    state: "unverified",
    lifecycleState: "proposed",
    reviewState: "unreviewed",
    confidence: null,
  };
}

export type BoxHandle = "nw" | "n" | "ne" | "e" | "se" | "s" | "sw" | "w" | "move";
// An outline point by its index, or the middle of the edge that starts at that point.
export type OutlineHandle = `vertex-${number}` | `midpoint-${number}`;
export type GeometryHandle = BoxHandle | OutlineHandle;

export function isOutlineHandle(handle: GeometryHandle): handle is OutlineHandle {
  return handle.startsWith("vertex-") || handle.startsWith("midpoint-");
}

export type BoxGeometry = Pick<AnnotationBox, "x" | "y" | "width" | "height">;

// Gives a box new geometry. An outline is mapped into the new box point by point, so
// the box stays the bounds of its outline.
export function withGeometry(box: AnnotationBox, geometry: BoxGeometry): AnnotationBox {
  const { x, y, width, height } = geometry;
  return {
    ...box,
    x,
    y,
    width,
    height,
    polygon: box.polygon && fitPolygon(box.polygon, box, geometry),
  };
}

function fitPolygon(
  polygon: PolygonPoint[],
  from: BoxGeometry,
  to: BoxGeometry,
): PolygonPoint[] {
  const scaleX = to.width / from.width;
  const scaleY = to.height / from.height;
  return polygon.map(([x, y]) => [to.x + (x - from.x) * scaleX, to.y + (y - from.y) * scaleY]);
}

// A pointer drag on a selected box: "move" shifts it, a handle moves the edges it names.
export function dragGeometry(
  original: BoxGeometry,
  handle: BoxHandle,
  deltaX: number,
  deltaY: number,
  bounds: SceneBounds,
): BoxGeometry {
  if (handle === "move") {
    return {
      ...original,
      x: clamp(original.x + deltaX, 0, bounds.width - original.width),
      y: clamp(original.y + deltaY, 0, bounds.height - original.height),
    };
  }
  let left = original.x;
  let top = original.y;
  let right = original.x + original.width;
  let bottom = original.y + original.height;
  if (handle.includes("w")) {
    left = clamp(left + deltaX, 0, right - MIN_BOX_SIZE);
  }
  if (handle.includes("e")) {
    right = clamp(right + deltaX, left + MIN_BOX_SIZE, bounds.width);
  }
  if (handle.includes("n")) {
    top = clamp(top + deltaY, 0, bottom - MIN_BOX_SIZE);
  }
  if (handle.includes("s")) {
    bottom = clamp(bottom + deltaY, top + MIN_BOX_SIZE, bounds.height);
  }
  return { x: left, y: top, width: right - left, height: bottom - top };
}

export type DrawnClass = Extract<AnnotationClass, "product" | "gap">;

export function createDrawnBox(
  classType: DrawnClass,
  id: string,
  imageId: string,
  imageIndex: number,
  start: ScenePoint,
  end: ScenePoint,
  bounds: SceneBounds,
  shelfRow: number,
): AnnotationBox | null {
  const left = clamp(Math.min(start.x, end.x), 0, bounds.width);
  const top = clamp(Math.min(start.y, end.y), 0, bounds.height);
  const right = clamp(Math.max(start.x, end.x), 0, bounds.width);
  const bottom = clamp(Math.max(start.y, end.y), 0, bounds.height);
  if (right - left < MIN_BOX_SIZE || bottom - top < MIN_BOX_SIZE) {
    return null;
  }
  return {
    id,
    serverId: null,
    imageId,
    revision: null,
    x: left,
    y: top,
    width: right - left,
    height: bottom - top,
    polygon: null,
    classType,
    state: "unverified",
    lifecycleState: "proposed",
    reviewState: "unreviewed",
    sku: classType === "gap" ? "Visible gap" : "Unknown SKU",
    skuId: null,
    confidence: null,
    occluded: false,
    truncated: false,
    shelfRow,
    imageIndex,
  };
}

// A product drawn as an outline. Its box is the outline's bounds, as the API requires.
export function createOutlinedBox(
  id: string,
  imageId: string,
  imageIndex: number,
  outline: PolygonPoint[],
  bounds: SceneBounds,
  shelfRow: number,
): AnnotationBox | null {
  const { x, y, width, height } = outlineBounds(outline);
  const box = createDrawnBox(
    "product",
    id,
    imageId,
    imageIndex,
    { x, y },
    { x: x + width, y: y + height },
    bounds,
    shelfRow,
  );
  return box && { ...box, polygon: outline };
}

export function outlineBounds(outline: PolygonPoint[]): BoxGeometry {
  const xs = outline.map(([x]) => x);
  const ys = outline.map(([, y]) => y);
  const x = Math.min(...xs);
  const y = Math.min(...ys);
  return { x, y, width: Math.max(...xs) - x, height: Math.max(...ys) - y };
}

// Gives a box a new outline; the box becomes the outline's bounds.
export function withOutline(box: AnnotationBox, outline: PolygonPoint[]): AnnotationBox {
  return { ...box, ...outlineBounds(outline), polygon: outline };
}

// A point placed on the canvas, kept on the photo it belongs to.
export function pointOnImage(point: ScenePoint, image: FixtureImage): PolygonPoint {
  return [
    clamp(point.x, image.x, image.x + image.width),
    clamp(point.y, image.y, image.y + image.height),
  ];
}

// Drags one point of an outline, kept on its photo. A midpoint handle first adds a point
// halfway along its edge, so the drag bends that edge. Null when the outline would
// shrink below the smallest box the canvas keeps.
export function dragOutlinePoint(
  outline: PolygonPoint[],
  handle: OutlineHandle,
  deltaX: number,
  deltaY: number,
  image: FixtureImage,
): PolygonPoint[] | null {
  const [kind, indexText] = handle.split("-");
  const index = Number(indexText);
  const points = kind === "midpoint" ? withMidpoint(outline, index) : [...outline];
  const moved = kind === "midpoint" ? index + 1 : index;
  const [x, y] = points[moved]!;
  points[moved] = pointOnImage({ x: x + deltaX, y: y + deltaY }, image);
  return keepsSize(points) ? points : null;
}

// The outline without one point, or null when that would leave fewer than three points
// or an outline below the smallest box.
export function removeOutlinePoint(outline: PolygonPoint[], index: number): PolygonPoint[] | null {
  if (outline.length <= 3) {
    return null;
  }
  const points = outline.filter((_, at) => at !== index);
  return keepsSize(points) ? points : null;
}

function withMidpoint(outline: PolygonPoint[], index: number): PolygonPoint[] {
  const [startX, startY] = outline[index]!;
  const [endX, endY] = outline[(index + 1) % outline.length]!;
  return [
    ...outline.slice(0, index + 1),
    [(startX + endX) / 2, (startY + endY) / 2],
    ...outline.slice(index + 1),
  ];
}

function keepsSize(outline: PolygonPoint[]): boolean {
  const { width, height } = outlineBounds(outline);
  return width >= MIN_BOX_SIZE && height >= MIN_BOX_SIZE;
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}
