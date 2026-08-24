import { memo } from "react";

import type { AnnotationBox } from "./model";
import { overlayCue } from "./model";

type AnnotationOverlayProps = {
  annotations: AnnotationBox[];
  width: number;
  height: number;
  selectedId: string | null;
  interactive: boolean;
  onSelect: (annotationId: string) => void;
};

type AnnotationNodeProps = {
  box: AnnotationBox;
  selected: boolean;
  interactive: boolean;
  onSelect: (annotationId: string) => void;
};

const HANDLE_SIZE = 8;

const AnnotationNode = memo(function AnnotationNode({
  box,
  selected,
  interactive,
  onSelect,
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
        if (event.button === 0 && interactive) {
          event.stopPropagation();
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
      {selected && <Selection box={box} />}
    </g>
  );
});

export const AnnotationOverlay = memo(function AnnotationOverlay({
  annotations,
  width,
  height,
  selectedId,
  interactive,
  onSelect,
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
          onSelect={onSelect}
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

function Selection({ box }: { box: AnnotationBox }) {
  const halfHandle = HANDLE_SIZE / 2;
  const handles: Array<[number, number]> = [
    [box.x, box.y],
    [box.x + box.width, box.y],
    [box.x, box.y + box.height],
    [box.x + box.width, box.y + box.height],
  ];
  return (
    <>
      <rect
        className="selection-outline"
        vectorEffect="non-scaling-stroke"
        {...rectGeometry(box)}
      />
      {handles.map(([x, y], index) => (
        <rect
          className="selection-handle"
          x={x - halfHandle}
          y={y - halfHandle}
          width={HANDLE_SIZE}
          height={HANDLE_SIZE}
          vectorEffect="non-scaling-stroke"
          key={index}
        />
      ))}
    </>
  );
}
