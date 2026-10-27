import type { PolygonDraft } from "./usePolygonDrawing";

type PolygonDraftOverlayProps = {
  draft: PolygonDraft;
  width: number;
  height: number;
  // The radius of a point in scene units, so points keep their on-screen size at any zoom.
  pointSize: number;
};

// The outline being drawn: its points, the edges between them, and an edge from the last
// point to the pointer. The first point grows once a click on it would close the outline.
export function PolygonDraftOverlay({ draft, width, height, pointSize }: PolygonDraftOverlayProps) {
  const path = draft.pointer
    ? [...draft.points, [draft.pointer.x, draft.pointer.y]]
    : draft.points;
  const closable = draft.points.length >= 3;
  return (
    <svg className="polygon-draft" viewBox={`0 0 ${width} ${height}`} aria-hidden="true">
      <polyline
        points={path.map(([x, y]) => `${x},${y}`).join(" ")}
        vectorEffect="non-scaling-stroke"
      />
      {draft.points.map(([x, y], index) => {
        const closing = index === 0 && closable;
        return (
          <circle
            className={closing ? "is-closing" : undefined}
            cx={x}
            cy={y}
            r={closing ? pointSize : pointSize / 2}
            vectorEffect="non-scaling-stroke"
            key={index}
          />
        );
      })}
    </svg>
  );
}
