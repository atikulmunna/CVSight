import type { AnnotationBox } from "./model";

export type SceneTransform = {
  scale: number;
  x: number;
  y: number;
};

export type ViewportSize = {
  width: number;
  height: number;
};

export const MIN_SCALE = 0.2;
export const MAX_SCALE = 4;

export function fitTransform(
  viewport: ViewportSize,
  scene: ViewportSize,
): SceneTransform {
  const scale = Math.min(viewport.width / scene.width, viewport.height / scene.height);
  return {
    scale,
    x: (viewport.width - scene.width * scale) / 2,
    y: (viewport.height - scene.height * scale) / 2,
  };
}

export function zoomTransform(
  current: SceneTransform,
  requestedScale: number,
  point: { x: number; y: number },
): SceneTransform {
  const scale = Math.min(MAX_SCALE, Math.max(MIN_SCALE, requestedScale));
  const imageX = (point.x - current.x) / current.scale;
  const imageY = (point.y - current.y) / current.scale;
  return {
    scale,
    x: point.x - imageX * scale,
    y: point.y - imageY * scale,
  };
}

export function revealBox(
  current: SceneTransform,
  viewport: ViewportSize,
  box: AnnotationBox,
  margin = 64,
): SceneTransform {
  const next = { ...current };
  const left = current.x + box.x * current.scale;
  const right = current.x + (box.x + box.width) * current.scale;
  const top = current.y + box.y * current.scale;
  const bottom = current.y + (box.y + box.height) * current.scale;

  if (left < margin) {
    next.x += margin - left;
  } else if (right > viewport.width - margin) {
    next.x -= right - (viewport.width - margin);
  }
  if (top < margin) {
    next.y += margin - top;
  } else if (bottom > viewport.height - margin) {
    next.y -= bottom - (viewport.height - margin);
  }
  return next;
}
