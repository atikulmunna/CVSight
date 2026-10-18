import {
  useRef,
  useState,
  type Dispatch,
  type PointerEvent,
  type RefObject,
  type SetStateAction,
  type WheelEvent,
} from "react";

import type { CanvasTool } from "./CanvasTools";
import {
  createDrawnBox,
  dragGeometry,
  localBoxId,
  type BoxGeometry,
  type DrawnClass,
  type GeometryHandle,
  type ScenePoint,
} from "./editing";
import type { AnnotationBox, CanvasFixture } from "./model";
import { boxesInside, rectBetween } from "./selection";
import { zoomTransform, type SceneTransform } from "./transform";

type PanStart = {
  pointerId: number;
  clientX: number;
  clientY: number;
  transformX: number;
  transformY: number;
};

type GeometryDrag = {
  pointerId: number;
  annotationId: string;
  handle: GeometryHandle;
  start: ScenePoint;
  original: BoxGeometry;
  moved: boolean;
};

type DrawStart = {
  pointerId: number;
  classType: DrawnClass;
  id: string;
  imageIndex: number;
  shelfRow: number;
  x: number;
  y: number;
};

export type Marquee = {
  pointerId: number;
  start: ScenePoint;
  end: ScenePoint;
  // Shift, Ctrl, or Cmd adds the boxes inside to the selection instead of replacing it.
  extend: boolean;
  moved: boolean;
  // A click, not a drag, on the space around the photo clears the selection.
  clearsOnClick: boolean;
};

// Pointer travel, in screen pixels, before a press counts as a drag.
const DRAG_THRESHOLD = 3;

type CanvasPointerOptions = {
  fixture: CanvasFixture;
  // The drawing tool in use, or null when drawing is not on offer.
  drawMode: DrawnClass | null;
  tool: CanvasTool;
  spaceHeld: boolean;
  transform: SceneTransform;
  setTransform: Dispatch<SetStateAction<SceneTransform>>;
  viewportRef: RefObject<HTMLDivElement | null>;
  activeAnnotations: AnnotationBox[];
  // The boxes a marquee may select.
  selectableAnnotations: AnnotationBox[];
  onBoxDrawn: (box: AnnotationBox) => void;
  onGeometryChanged: (annotationId: string, geometry: BoxGeometry) => void;
  // A press on the editable box that ended without a drag.
  onBoxClicked: (annotationId: string) => void;
  onMarqueeSelect: (ids: string[], extend: boolean) => void;
  onBackgroundClick: () => void;
};

// Turns pointer and wheel input on the canvas into pans, zooms, drawn boxes, geometry
// edits, and marquee selections. What those mean for annotations stays with the caller.
export function useCanvasPointer({
  fixture,
  drawMode,
  tool,
  spaceHeld,
  transform,
  setTransform,
  viewportRef,
  activeAnnotations,
  selectableAnnotations,
  onBoxDrawn,
  onGeometryChanged,
  onBoxClicked,
  onMarqueeSelect,
  onBackgroundClick,
}: CanvasPointerOptions) {
  const [panStart, setPanStart] = useState<PanStart | null>(null);
  const [drawStart, setDrawStart] = useState<DrawStart | null>(null);
  const [draftBox, setDraftBox] = useState<AnnotationBox | null>(null);
  const [geometryDrag, setGeometryDrag] = useState<GeometryDrag | null>(null);
  const [draftGeometry, setDraftGeometry] = useState<(BoxGeometry & { id: string }) | null>(
    null,
  );
  const [marquee, setMarquee] = useState<Marquee | null>(null);
  const crosshairRef = useRef<HTMLDivElement>(null);

  function cancelDrawing() {
    setDrawStart(null);
    setDraftBox(null);
  }

  function handlePointerDown(event: PointerEvent<HTMLDivElement>) {
    // Toolbars and the SKU picker sit inside the viewport; their clicks are theirs.
    if (isControlTarget(event.target)) {
      return;
    }
    const shouldPan =
      event.button === 1 || (event.button === 0 && (spaceHeld || tool === "pan"));
    if (drawMode && event.button === 0 && !spaceHeld) {
      startDrawing(event, drawMode);
      return;
    }
    if (!shouldPan) {
      if (event.button === 0 && tool === "select") {
        startMarquee(event);
      }
      return;
    }
    event.preventDefault();
    event.currentTarget.setPointerCapture(event.pointerId);
    setPanStart({
      pointerId: event.pointerId,
      clientX: event.clientX,
      clientY: event.clientY,
      transformX: transform.x,
      transformY: transform.y,
    });
  }

  function startDrawing(event: PointerEvent<HTMLDivElement>, classType: DrawnClass) {
    event.preventDefault();
    const point = scenePoint(event, transform);
    const imageIndex = imageIndexAtPoint(fixture, point);
    if (imageIndex < 0) {
      return;
    }
    event.currentTarget.setPointerCapture(event.pointerId);
    const start: DrawStart = {
      pointerId: event.pointerId,
      classType,
      id: localBoxId(classType),
      imageIndex,
      shelfRow: nearestShelfRow(activeAnnotations, point.y),
      ...point,
    };
    setDrawStart(start);
    setDraftBox(drawnBox(start, { x: point.x + 4, y: point.y + 4 }));
  }

  function startMarquee(event: PointerEvent<HTMLDivElement>) {
    event.currentTarget.setPointerCapture?.(event.pointerId);
    const point = scenePoint(event, transform);
    setMarquee({
      pointerId: event.pointerId,
      start: point,
      end: point,
      extend: event.shiftKey || event.ctrlKey || event.metaKey,
      moved: false,
      clearsOnClick: event.target === event.currentTarget,
    });
  }

  function drawnBox(start: DrawStart, end: ScenePoint) {
    return createDrawnBox(
      start.classType,
      start.id,
      fixture.images[start.imageIndex]!.id,
      start.imageIndex,
      start,
      end,
      fixture,
      start.shelfRow,
    );
  }

  function startGeometryDrag(
    annotationId: string,
    handle: GeometryHandle,
    event: PointerEvent<SVGGElement>,
  ) {
    const box = activeAnnotations.find((annotation) => annotation.id === annotationId);
    const viewport = viewportRef.current;
    if (!box || !viewport) {
      return;
    }
    viewport.setPointerCapture?.(event.pointerId);
    const bounds = viewport.getBoundingClientRect();
    setGeometryDrag({
      pointerId: event.pointerId,
      annotationId,
      handle,
      start: {
        x: (event.clientX - bounds.left - transform.x) / transform.scale,
        y: (event.clientY - bounds.top - transform.y) / transform.scale,
      },
      original: { x: box.x, y: box.y, width: box.width, height: box.height },
      moved: false,
    });
  }

  // The geometry a drag reaches at this pointer position, or null while it is still
  // within the click threshold.
  function draggedGeometry(drag: GeometryDrag, event: PointerEvent<HTMLDivElement>) {
    const point = scenePoint(event, transform);
    if (!drag.moved && !dragged(drag.start, point)) {
      return null;
    }
    return dragGeometry(
      drag.original,
      drag.handle,
      point.x - drag.start.x,
      point.y - drag.start.y,
      fixture,
    );
  }

  // Whether pointer travel between two scene points is a drag rather than a click.
  function dragged(start: ScenePoint, end: ScenePoint): boolean {
    return Math.hypot(end.x - start.x, end.y - start.y) * transform.scale >= DRAG_THRESHOLD;
  }

  // The crosshair follows the pointer through the DOM, so hovering never re-renders the
  // canvas.
  function moveCrosshair(event: PointerEvent<HTMLDivElement>) {
    const crosshair = crosshairRef.current;
    if (!crosshair) {
      return;
    }
    const bounds = event.currentTarget.getBoundingClientRect();
    crosshair.style.setProperty("--crosshair-x", `${event.clientX - bounds.left}px`);
    crosshair.style.setProperty("--crosshair-y", `${event.clientY - bounds.top}px`);
    crosshair.classList.add("is-visible");
  }

  function handlePointerMove(event: PointerEvent<HTMLDivElement>) {
    moveCrosshair(event);
    if (geometryDrag?.pointerId === event.pointerId) {
      const geometry = draggedGeometry(geometryDrag, event);
      if (geometry) {
        if (!geometryDrag.moved) {
          setGeometryDrag({ ...geometryDrag, moved: true });
        }
        setDraftGeometry({ id: geometryDrag.annotationId, ...geometry });
      }
      return;
    }
    if (drawStart?.pointerId === event.pointerId) {
      setDraftBox(drawnBox(drawStart, scenePoint(event, transform)));
      return;
    }
    if (marquee?.pointerId === event.pointerId) {
      const end = scenePoint(event, transform);
      setMarquee({ ...marquee, end, moved: marquee.moved || dragged(marquee.start, end) });
      return;
    }
    if (!panStart || panStart.pointerId !== event.pointerId) {
      return;
    }
    setTransform((current) => ({
      ...current,
      x: panStart.transformX + event.clientX - panStart.clientX,
      y: panStart.transformY + event.clientY - panStart.clientY,
    }));
  }

  function finishPointerAction(event: PointerEvent<HTMLDivElement>) {
    if (geometryDrag?.pointerId === event.pointerId) {
      // Final geometry comes from the release point, so it never trails the last move.
      const geometry = draggedGeometry(geometryDrag, event);
      if (geometry) {
        onGeometryChanged(geometryDrag.annotationId, geometry);
      } else if (geometryDrag.handle === "move") {
        onBoxClicked(geometryDrag.annotationId);
      }
      setGeometryDrag(null);
      setDraftGeometry(null);
      return;
    }
    if (drawStart?.pointerId === event.pointerId) {
      const completed = drawnBox(drawStart, scenePoint(event, transform));
      if (completed) {
        onBoxDrawn(completed);
      }
      cancelDrawing();
      return;
    }
    if (marquee?.pointerId === event.pointerId) {
      finishMarquee(marquee, scenePoint(event, transform));
      setMarquee(null);
      return;
    }
    if (panStart?.pointerId === event.pointerId) {
      setPanStart(null);
    }
  }

  function finishMarquee(current: Marquee, end: ScenePoint) {
    if (!current.moved && !dragged(current.start, end)) {
      if (current.clearsOnClick) {
        onBackgroundClick();
      }
      return;
    }
    onMarqueeSelect(
      boxesInside(selectableAnnotations, rectBetween(current.start, end)),
      current.extend,
    );
  }

  function handleWheel(event: WheelEvent<HTMLDivElement>) {
    event.preventDefault();
    const bounds = event.currentTarget.getBoundingClientRect();
    const multiplier = Math.exp(-event.deltaY * 0.0015);
    setTransform((current) =>
      zoomTransform(current, current.scale * multiplier, {
        x: event.clientX - bounds.left,
        y: event.clientY - bounds.top,
      }),
    );
  }

  return {
    panning: panStart !== null,
    draftBox,
    draftGeometry,
    marquee,
    crosshairRef,
    cancelDrawing,
    startGeometryDrag,
    handlers: {
      onPointerDown: handlePointerDown,
      onPointerMove: handlePointerMove,
      onPointerUp: finishPointerAction,
      onPointerCancel: finishPointerAction,
      onPointerLeave: () => crosshairRef.current?.classList.remove("is-visible"),
      onWheel: handleWheel,
    },
  };
}

function isControlTarget(target: EventTarget | null): boolean {
  return target instanceof Element && target.closest("button, input, select, textarea") !== null;
}

function scenePoint(event: PointerEvent<HTMLDivElement>, transform: SceneTransform): ScenePoint {
  const bounds = event.currentTarget.getBoundingClientRect();
  return {
    x: (event.clientX - bounds.left - transform.x) / transform.scale,
    y: (event.clientY - bounds.top - transform.y) / transform.scale,
  };
}

function imageIndexAtPoint(fixture: CanvasFixture, point: ScenePoint): number {
  return fixture.images.findIndex(
    (image) =>
      point.x >= image.x &&
      point.x <= image.x + image.width &&
      point.y >= image.y &&
      point.y <= image.y + image.height,
  );
}

function nearestShelfRow(annotations: AnnotationBox[], y: number): number {
  const rowCandidates = annotations.filter((box) => box.shelfRow !== null);
  if (rowCandidates.length === 0) {
    return 0;
  }
  return rowCandidates.reduce((closest, box) =>
    Math.abs(box.y + box.height / 2 - y) < Math.abs(closest.y + closest.height / 2 - y)
      ? box
      : closest,
  ).shelfRow!;
}
