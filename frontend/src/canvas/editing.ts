import type { AnnotationBox, AnnotationClass } from "./model";

export type EditIntent = "accept" | "reject" | "update" | "create" | "assign";

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
    : { ...box, x, y, confidence: null };
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
    : { ...box, width, height, confidence: null };
}

export function duplicateBox(
  box: AnnotationBox,
  id: string,
  bounds: SceneBounds,
): AnnotationBox {
  const x = clamp(box.x + DUPLICATE_OFFSET, 0, bounds.width - box.width);
  const y = clamp(box.y + DUPLICATE_OFFSET, 0, bounds.height - box.height);
  return {
    ...box,
    id,
    serverId: null,
    revision: null,
    x,
    y,
    state: "unverified",
    lifecycleState: "proposed",
    reviewState: "unreviewed",
    confidence: null,
  };
}

export function createGapBox(
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
    classType: "gap",
    state: "unverified",
    lifecycleState: "proposed",
    reviewState: "unreviewed",
    sku: "Visible gap",
    skuId: null,
    confidence: null,
    occluded: false,
    truncated: false,
    shelfRow,
    imageIndex,
  };
}

function clamp(value: number, minimum: number, maximum: number): number {
  return Math.min(Math.max(value, minimum), maximum);
}
