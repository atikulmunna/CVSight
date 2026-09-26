import { memo, type PointerEvent } from "react";

import type { GeometryHandle } from "./editing";
import type { AnnotationBox } from "./model";
import { overlayCue } from "./model";

type GeometryStart = (
  annotationId: string,
  handle: GeometryHandle,
  event: PointerEvent<SVGGElement>,
) => void;

type AnnotationOverlayProps = {
  annotations: AnnotationBox[];
  width: number;
  height: number;
  selectedId: string | null;
  interactive: boolean;
  handleSize?: number;
  handlesActive?: boolean;
  onSelect: (annotationId: string) => void;
  onGeometryStart?: GeometryStart;
};

type AnnotationNodeProps = {
  box: AnnotationBox;
  selected: boolean;
  interactive: boolean;
  handleSize: number;
  handlesActive: boolean;
  onSelect: (annotationId: string) => void;
  onGeometryStart?: GeometryStart;
};

const AnnotationNode = memo(function AnnotationNode({
  box,
  selected,
  interactive,
  handleSize,
  handlesActive,
  onSelect,
  onGeometryStart,
}: AnnotationNodeProps) {
  return (
    <g
      className={`annotation${selected ? " is-selected" : ""}`}
      data-annotation-id={box.id}
      data-state={box.state}
      data-class={box.classType}
      data-nonhue-cue={overlayCue(box)}
      role="button"
      aria-label={`${box.id}, ${box.sku}, ${box.state}`}
      onPointerDown={(event) => {
        if (event.button !== 0) {
          return;
        }
        // Handles resize even while a drawing tool is active; the body moves only in
        // Select, where a first click selects and a drag on the selected box moves it.
        const handle = handleAt(event.target);
        if (selected && handle && handlesActive && onGeometryStart) {
          event.stopPropagation();
          onGeometryStart(box.id, handle, event);
          return;
        }
        if (!interactive) {
          return;
        }
        event.stopPropagation();
        if (selected && onGeometryStart) {
          onGeometryStart(box.id, "move", event);
        } else {
          onSelect(box.id);
        }
      }}
    >
      <rect
        className="overlay-halo"
        vectorEffect="non-scaling-stroke"
        {...rectGeometry(box)}
      />
      <rect
        className="overlay-core"
        vectorEffect="non-scaling-stroke"
        {...rectGeometry(box)}
      />
      {box.state === "propagated" && (
        <>
          <rect
            className="overlay-secondary-halo"
            vectorEffect="non-scaling-stroke"
            x={box.x - 3}
            y={box.y - 3}
            width={box.width + 6}
            height={box.height + 6}
          />
          <rect
            className="overlay-secondary"
            vectorEffect="non-scaling-stroke"
            x={box.x - 3}
            y={box.y - 3}
            width={box.width + 6}
            height={box.height + 6}
          />
        </>
      )}
      {selected && <Selection box={box} handleSize={handleSize} />}
    </g>
  );
});

export const AnnotationOverlay = memo(function AnnotationOverlay({
  annotations,
  width,
  height,
  selectedId,
  interactive,
  handleSize = 8,
  handlesActive = false,
  onSelect,
  onGeometryStart,
}: AnnotationOverlayProps) {
  return (
    <svg
      className={`annotation-overlay${selectedId ? " has-selection" : ""}`}
      viewBox={`0 0 ${width} ${height}`}
      aria-label="Annotation overlays"
      data-overlay-count={annotations.length}
    >
      <defs>
        <pattern
          id="gap-hatch"
          width="8"
          height="8"
          patternUnits="userSpaceOnUse"
          patternTransform="rotate(45)"
        >
          <line
            x1="0"
            y1="0"
            x2="0"
            y2="8"
            stroke="var(--ov-gap)"
            strokeOpacity="0.45"
            strokeWidth="2"
          />
        </pattern>
      </defs>
      {annotations.map((box) => (
        <AnnotationNode
          box={box}
          selected={box.id === selectedId}
          interactive={interactive}
          handleSize={box.id === selectedId ? handleSize : 0}
          handlesActive={handlesActive}
          onSelect={onSelect}
          onGeometryStart={onGeometryStart}
          key={box.id}
        />
      ))}
    </svg>
  );
});

function rectGeometry(box: AnnotationBox) {
  return {
    x: box.x,
    y: box.y,
    width: box.width,
    height: box.height,
  };
}

const HANDLES: Array<[GeometryHandle, number, number]> = [
  ["nw", 0, 0],
  ["n", 0.5, 0],
  ["ne", 1, 0],
  ["e", 1, 0.5],
  ["se", 1, 1],
  ["s", 0.5, 1],
  ["sw", 0, 1],
  ["w", 0, 0.5],
];

function handleAt(target: EventTarget | null): GeometryHandle | null {
  const handle =
    target instanceof Element ? target.closest("[data-handle]")?.getAttribute("data-handle") : null;
  return (HANDLES.find(([name]) => name === handle)?.[0] ?? null);
}

// handleSize is in scene units, chosen by the workspace so handles keep a constant
// on-screen size at any zoom; each has a wider invisible hit area.
function Selection({ box, handleSize }: { box: AnnotationBox; handleSize: number }) {
  return (
    <>
      <rect
        className="selection-outline"
        vectorEffect="non-scaling-stroke"
        {...rectGeometry(box)}
      />
      {HANDLES.map(([name, fx, fy]) => {
        const x = box.x + box.width * fx;
        const y = box.y + box.height * fy;
        return (
          <g key={name}>
            <rect
              className="selection-handle-hit"
              data-handle={name}
              x={x - handleSize}
              y={y - handleSize}
              width={handleSize * 2}
              height={handleSize * 2}
            />
            <rect
              className="selection-handle"
              x={x - handleSize / 2}
              y={y - handleSize / 2}
              width={handleSize}
              height={handleSize}
              vectorEffect="non-scaling-stroke"
            />
          </g>
        );
      })}
    </>
  );
}
