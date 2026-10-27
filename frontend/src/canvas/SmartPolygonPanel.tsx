type SmartPolygonPanelProps = {
  pointCount: number;
  maxPointCount: number;
  onPointCountChange: (pointCount: number) => void;
  onFinish: () => void;
  onCancel: () => void;
};

// Refines the outline being drawn with the smart polygon before it is saved. It sits on
// the canvas, so its own pointer presses must not reach the canvas as clicks.
export function SmartPolygonPanel({
  pointCount,
  maxPointCount,
  onPointCountChange,
  onFinish,
  onCancel,
}: SmartPolygonPanelProps) {
  return (
    <div
      className="smart-polygon-panel"
      role="group"
      aria-label="Smart polygon"
      onPointerDown={(event) => event.stopPropagation()}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== "Escape") {
          return;
        }
        event.preventDefault();
        event.stopPropagation();
        if (event.key === "Enter") {
          onFinish();
        } else {
          onCancel();
        }
      }}
    >
      <label>
        <span>
          Points <output>{pointCount}</output>
        </span>
        <input
          aria-label="Points"
          type="range"
          min={3}
          max={maxPointCount}
          value={pointCount}
          onChange={(event) => onPointCountChange(Number(event.target.value))}
        />
      </label>
      <p>Click to add an area, Shift+click to remove one.</p>
      <div className="smart-polygon-actions">
        <button type="button" onClick={onCancel}>
          Cancel <kbd>Esc</kbd>
        </button>
        <button type="button" className="primary-action" onClick={onFinish}>
          Finish <kbd>Enter</kbd>
        </button>
      </div>
    </div>
  );
}
