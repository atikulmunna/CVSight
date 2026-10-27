import { useMemo, useRef, useState } from "react";

import { createOutlinedBox, localBoxId, type ScenePoint } from "./editing";
import type { AnnotationBox, CanvasFixture } from "./model";
import { nearestShelfRow } from "./model";
import { defaultPointCount, outlineWithPoints, rankOutline, type RankedOutline } from "./outline";
import { requestSmartPolygon, smartBoxMessage, SmartBoxError, type SmartClick } from "./smartBoxApi";

// The API takes at most this many clicks for one outline.
const MAX_CLICKS = 16;

type SmartPolygonOptions = {
  fixture: CanvasFixture;
  activeAnnotations: AnnotationBox[];
  // Receives the finished product exactly as a drawn box would arrive.
  onBox: (box: AnnotationBox) => void;
};

// The outline being refined: the clicks so far, SAM's ranked answer, and the point count.
export type OutlineDraft = {
  imageIndex: number;
  clicks: SmartClick[];
  outline: RankedOutline;
  pointCount: number;
};

// Outlines the product under a click and lets the reviewer refine it before it is saved:
// a click adds an area, a Shift+click removes one, and the point count thins the outline.
// One request runs at a time; clicks while it is pending are ignored.
export function useSmartPolygon({ fixture, activeAnnotations, onBox }: SmartPolygonOptions) {
  const [draft, setDraft] = useState<OutlineDraft | null>(null);
  const [pendingPoint, setPendingPoint] = useState<ScenePoint | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  // Finishing or cancelling discards the answer to a request still on its way.
  const latestRequest = useRef(0);

  // The draft as the product it becomes, drawn on the canvas while it is refined.
  const preview = useMemo(() => {
    if (!draft) {
      return null;
    }
    const image = fixture.images[draft.imageIndex]!;
    return createOutlinedBox(
      "smart-polygon-draft",
      image.id,
      draft.imageIndex,
      outlineWithPoints(draft.outline, draft.pointCount),
      fixture,
      0,
    );
  }, [draft, fixture]);

  async function click(point: ScenePoint, imageIndex: number, include: boolean) {
    if (pendingPoint) {
      return;
    }
    if (draft && draft.imageIndex !== imageIndex) {
      setMessage("Finish this outline first: press Enter.");
      return;
    }
    if (!draft && !include) {
      setMessage("Click the product first; Shift+click then removes areas from its outline.");
      return;
    }
    const clicks = [...(draft?.clicks ?? []), { ...point, include }];
    if (clicks.length > MAX_CLICKS) {
      setMessage(`An outline takes at most ${MAX_CLICKS} clicks. Finish it or start over.`);
      return;
    }
    const image = fixture.images[imageIndex]!;
    const request = ++latestRequest.current;
    setPendingPoint(point);
    setMessage(null);
    try {
      const polygon = await requestSmartPolygon(
        image.id,
        clicks.map((each) => ({ x: each.x - image.x, y: each.y - image.y, include: each.include })),
      );
      if (request !== latestRequest.current) {
        return;
      }
      const outline = rankOutline(polygon.map(([x, y]) => [image.x + x, image.y + y]));
      setDraft({ imageIndex, clicks, outline, pointCount: defaultPointCount(outline) });
    } catch (error) {
      if (request === latestRequest.current) {
        setMessage(smartBoxMessage(error, "Smart polygon"));
      }
    } finally {
      if (request === latestRequest.current) {
        setPendingPoint(null);
      }
    }
  }

  function finish() {
    discard();
    if (!draft) {
      return;
    }
    if (!preview) {
      // SAM's outline is smaller than the smallest box the canvas keeps.
      setMessage(smartBoxMessage(new SmartBoxError(422, "no_object"), "Smart polygon"));
      return;
    }
    onBox({
      ...preview,
      id: localBoxId("smart"),
      shelfRow: nearestShelfRow(activeAnnotations, draft.clicks[0]!.y),
    });
  }

  function discard() {
    latestRequest.current += 1;
    setDraft(null);
    setPendingPoint(null);
    setMessage(null);
  }

  function setPointCount(pointCount: number) {
    setDraft((current) => current && { ...current, pointCount });
  }

  return { draft, preview, pendingPoint, message, click, finish, cancel: discard, setPointCount };
}
