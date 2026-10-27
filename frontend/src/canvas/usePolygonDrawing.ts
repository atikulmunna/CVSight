import { useState } from "react";

import { createOutlinedBox, localBoxId, pointOnImage, type ScenePoint } from "./editing";
import {
  MAX_POLYGON_POINTS,
  nearestShelfRow,
  type AnnotationBox,
  type CanvasFixture,
  type PolygonPoint,
} from "./model";

type PolygonDrawingOptions = {
  fixture: CanvasFixture;
  activeAnnotations: AnnotationBox[];
  // Receives the finished product exactly as a drawn box would arrive.
  onBox: (box: AnnotationBox) => void;
};

// The outline being drawn: its points so far, all on one photo, and the pointer that the
// next edge follows.
export type PolygonDraft = {
  imageIndex: number;
  points: PolygonPoint[];
  pointer: ScenePoint | null;
};

// Draws a product outline point by point. Each click adds a point; a click on the first
// point, or finishing, closes the outline once it has three points.
export function usePolygonDrawing({ fixture, activeAnnotations, onBox }: PolygonDrawingOptions) {
  const [draft, setDraft] = useState<PolygonDraft | null>(null);

  // closeRadius is how near the first point, in scene units, a click closes the outline.
  function click(point: ScenePoint, imageIndex: number, closeRadius: number) {
    if (!draft) {
      if (imageIndex >= 0) {
        const image = fixture.images[imageIndex]!;
        setDraft({ imageIndex, points: [pointOnImage(point, image)], pointer: point });
      }
      return;
    }
    const added = pointOnImage(point, fixture.images[draft.imageIndex]!);
    const [firstX, firstY] = draft.points[0]!;
    if (draft.points.length >= 3 && Math.hypot(added[0] - firstX, added[1] - firstY) <= closeRadius) {
      finish();
      return;
    }
    if (draft.points.length < MAX_POLYGON_POINTS) {
      setDraft({ ...draft, points: [...draft.points, added] });
    }
  }

  function move(pointer: ScenePoint) {
    setDraft((current) => current && { ...current, pointer });
  }

  function removeLastPoint() {
    setDraft((current) =>
      current && current.points.length > 1
        ? { ...current, points: current.points.slice(0, -1) }
        : null,
    );
  }

  // Fewer than three points make no outline, so finishing them only ends the drawing.
  function finish() {
    setDraft(null);
    if (!draft || draft.points.length < 3) {
      return;
    }
    const image = fixture.images[draft.imageIndex]!;
    const box = createOutlinedBox(
      localBoxId("polygon"),
      image.id,
      draft.imageIndex,
      draft.points,
      fixture,
      nearestShelfRow(activeAnnotations, draft.points[0]![1]),
    );
    if (box) {
      onBox(box);
    }
  }

  return { draft, click, move, removeLastPoint, finish, cancel: () => setDraft(null) };
}
