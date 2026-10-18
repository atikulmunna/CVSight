import { useState } from "react";

import { createDrawnBox, localBoxId, type ScenePoint } from "./editing";
import type { AnnotationBox, CanvasFixture } from "./model";
import { nearestShelfRow } from "./model";
import { requestSmartBox, SmartBoxError, smartBoxMessage } from "./smartBoxApi";

type SmartBoxOptions = {
  fixture: CanvasFixture;
  activeAnnotations: AnnotationBox[];
  // Receives the new product box exactly as a drawn one would arrive.
  onBox: (box: AnnotationBox) => void;
};

// Asks the API for the box around the product under a click. One request runs at a
// time; clicks while it is pending are ignored.
export function useSmartBox({ fixture, activeAnnotations, onBox }: SmartBoxOptions) {
  const [pendingPoint, setPendingPoint] = useState<ScenePoint | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function request(point: ScenePoint, imageIndex: number) {
    if (pendingPoint) {
      return;
    }
    const image = fixture.images[imageIndex]!;
    setPendingPoint(point);
    setMessage(null);
    try {
      const found = await requestSmartBox(image.id, {
        x: point.x - image.x,
        y: point.y - image.y,
      });
      const box = createDrawnBox(
        "product",
        localBoxId("smart"),
        image.id,
        imageIndex,
        { x: image.x + found.x, y: image.y + found.y },
        { x: image.x + found.x + found.width, y: image.y + found.y + found.height },
        fixture,
        nearestShelfRow(activeAnnotations, point.y),
      );
      if (box) {
        onBox(box);
      } else {
        // SAM found something smaller than the smallest box the canvas keeps.
        setMessage(smartBoxMessage(new SmartBoxError(422, "no_object")));
      }
    } catch (error) {
      setMessage(smartBoxMessage(error));
    } finally {
      setPendingPoint(null);
    }
  }

  return { pendingPoint, message, request };
}
