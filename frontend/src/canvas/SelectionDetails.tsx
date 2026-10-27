import type { AnnotationBox, AnnotationClass, CanvasFixture } from "./model";
import { overlayCue, skuLabel } from "./model";
import { cropStyle } from "./crop";

export function SelectionDetails({
  fixture,
  box,
  onAccept,
  onReject,
  onFlag,
  onDuplicate,
  onShelfRowChange,
  onClassChange,
  showVerificationActions,
  lockGapClass,
}: {
  fixture: CanvasFixture;
  box: AnnotationBox;
  onAccept: () => void;
  onReject: () => void;
  onFlag: () => void;
  onDuplicate: () => void;
  onShelfRowChange: (shelfRow: number | null) => void;
  onClassChange: (classType: AnnotationClass) => void;
  showVerificationActions: boolean;
  lockGapClass: boolean;
}) {
  const backgroundScale = Math.max(2, Math.min(5, 150 / Math.max(box.width, 30)));
  return (
    <div className="selection-details">
      <div
        className="selected-crop"
        aria-label="Selected product crop"
        style={cropStyle(
          box,
          fixture.images[box.imageIndex]!,
          { width: 280, height: 160 },
          backgroundScale,
        )}
      />
      {showVerificationActions && (
        <div className="verification-actions" aria-label="Box verification actions">
          <button
            type="button"
            className="primary-action"
            onClick={onAccept}
            disabled={
              box.lifecycleState === "verified" && box.reviewState === "accepted"
            }
          >
            {box.lifecycleState === "verified" && box.reviewState === "accepted"
              ? "Accepted"
              : "Accept"} <kbd>A</kbd>
          </button>
          <button type="button" className="reject-action" onClick={onReject}>
            Reject <kbd>R</kbd>
          </button>
          <button type="button" onClick={onFlag}>
            Flag <kbd>F</kbd>
          </button>
          <button type="button" onClick={onDuplicate}>
            Duplicate <kbd>D</kbd>
          </button>
        </div>
      )}
      <dl>
        <div>
          <dt>{box.classType === "gap" ? "Origin" : "Assigned SKU"}</dt>
          <dd>
            {box.classType === "gap"
              ? box.confidence === null
                ? "Human drawn or corrected"
                : "Initial geometry candidate"
              : skuLabel(box)}
          </dd>
        </div>
        <div>
          <dt>Workflow</dt>
          <dd>
            {box.state}, {overlayCue(box)} cue
          </dd>
        </div>
        <div>
          <dt>Class</dt>
          <dd>
            {lockGapClass ? (
              "Visible gap"
            ) : (
              <ClassSelect value={box.classType} onChange={onClassChange} />
            )}
          </dd>
        </div>
        <div>
          <dt>Shelf row</dt>
          <dd>
            <input
              className="shelf-row-input"
              aria-label="Shelf row number"
              type="number"
              min="0"
              step="1"
              placeholder="Auto"
              value={box.shelfRow ?? ""}
              onChange={(event) => {
                const value = event.currentTarget.value;
                if (value === "") {
                  onShelfRowChange(null);
                  return;
                }
                const row = Number(value);
                if (Number.isInteger(row) && row >= 0) {
                  onShelfRowChange(row);
                }
              }}
            />
          </dd>
        </div>
        <div>
          <dt>{box.classType === "gap" ? "Candidate score" : "Confidence"}</dt>
          <dd>
            {box.confidence === null ? "Not applicable" : box.confidence.toFixed(2)}
          </dd>
        </div>
        <div>
          <dt>Bounding box</dt>
          <dd>
            {Math.round(box.x)}, {Math.round(box.y)}, {Math.round(box.width)},{" "}
            {Math.round(box.height)}
          </dd>
        </div>
      </dl>
    </div>
  );
}

// Several boxes share the actions of one; geometry stays a single-box edit.
export function GroupSelectionDetails({
  boxes,
  onAccept,
  onReject,
  onFlag,
  onDuplicate,
  onClassChange,
  showVerificationActions,
  lockGapClass,
}: {
  boxes: AnnotationBox[];
  onAccept: () => void;
  onReject: () => void;
  onFlag: () => void;
  onDuplicate: () => void;
  onClassChange: (classType: AnnotationClass) => void;
  showVerificationActions: boolean;
  lockGapClass: boolean;
}) {
  const accepted = boxes.filter(
    (box) => box.lifecycleState === "verified" && box.reviewState === "accepted",
  ).length;
  const classTypes = new Set(boxes.map((box) => box.classType));
  return (
    <div className="selection-details">
      <p className="group-summary">
        {boxes.length} boxes, {accepted} accepted. Actions apply to all of them. Select one
        box to move or resize it.
      </p>
      {showVerificationActions && (
        <div className="verification-actions" aria-label="Box verification actions">
          <button
            type="button"
            className="primary-action"
            onClick={onAccept}
            disabled={accepted === boxes.length}
          >
            Accept all <kbd>A</kbd>
          </button>
          <button type="button" className="reject-action" onClick={onReject}>
            Reject all <kbd>R</kbd>
          </button>
          <button type="button" onClick={onFlag}>
            Flag all <kbd>F</kbd>
          </button>
          <button type="button" onClick={onDuplicate}>
            Duplicate all <kbd>D</kbd>
          </button>
        </div>
      )}
      <dl>
        <div>
          <dt>Class</dt>
          <dd>
            {lockGapClass ? (
              "Visible gap"
            ) : (
              <ClassSelect
                value={classTypes.size === 1 ? boxes[0]!.classType : null}
                onChange={onClassChange}
              />
            )}
          </dd>
        </div>
      </dl>
    </div>
  );
}

// A null value means the selected boxes have different classes.
function ClassSelect({
  value,
  onChange,
}: {
  value: AnnotationClass | null;
  onChange: (classType: AnnotationClass) => void;
}) {
  return (
    <select
      className="annotation-class-select"
      aria-label="Annotation class"
      value={value ?? ""}
      onChange={(event) => onChange(event.currentTarget.value as AnnotationClass)}
    >
      {value === null && (
        <option value="" disabled>
          Mixed
        </option>
      )}
      <option value="product">Product</option>
      <option value="gap">Visible gap</option>
      <option value="shelf_label">Shelf label</option>
    </select>
  );
}
