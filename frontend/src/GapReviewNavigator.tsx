import type { GapReviewManifest } from "./gapReview";
import type { SaveStatus } from "./canvas/useAnnotationAutosave";

type GapReviewNavigatorProps = {
  manifest: GapReviewManifest;
  index: number;
  busy: boolean;
  reviewed: boolean;
  unresolvedCount: number;
  saveStatus: SaveStatus;
  error: string | null;
  onSelect: (index: number) => void;
  onMarkReviewed: () => void;
};

export function GapReviewNavigator({
  manifest,
  index,
  busy,
  reviewed,
  unresolvedCount,
  saveStatus,
  error,
  onSelect,
  onMarkReviewed,
}: GapReviewNavigatorProps) {
  const item = manifest.items[index]!;
  const isBlindTruth = manifest.reviewMode === "blind-truth";
  const canMarkReviewed =
    !busy && !reviewed && unresolvedCount === 0 && saveStatus === "saved";
  const markLabel = reviewed
    ? "Image reviewed"
    : unresolvedCount > 0
      ? `${unresolvedCount} decisions remaining`
      : saveStatus === "saving"
        ? "Saving changes"
        : saveStatus === "conflict"
          ? "Resolve save conflict"
          : saveStatus === "offline"
            ? "Waiting for connection"
            : isBlindTruth
              ? "Confirm image reviewed"
              : "Mark reviewed";
  return (
    <section className="gap-review-navigator" aria-label="Gap evidence review">
      <div>
        <span className="eyebrow">
          {isBlindTruth ? "BLIND GROUND TRUTH" : "HUMAN EVIDENCE GATE"}
        </span>
        <strong>
          {isBlindTruth ? "Image" : `${item.split} image`} {item.position} of{" "}
          {manifest.items.length}
        </strong>
      </div>
      <p>
        {isBlindTruth ? (
          <>
            Predictions are hidden. Inspect every product-bearing shelf row. Draw each
            genuine empty product slot with <kbd>G</kbd>, set its row, and accept it. If
            there are none, confirm the image as reviewed.
          </>
        ) : item.candidateCount === 0 ? (
          <>
            No gap was proposed. If the image truly has no shelf-row gap, mark it
            reviewed. Otherwise press <kbd>G</kbd> and drag over each missed gap.
          </>
        ) : (
          <>
            Correct every candidate, draw missed gaps with <kbd>G</kbd>, assign its shelf
            row, then accept it. Wait for “All changes saved”, then mark the image
            reviewed.
          </>
        )}
      </p>
      <div className="gap-review-navigation-actions">
        <span className={item.candidateCount === 0 ? "is-empty" : ""}>
          {isBlindTruth
            ? "Predictions hidden"
            : item.candidateCount === 0
            ? "No initial candidates"
            : `${item.candidateCount} initial candidates`}
        </span>
        <button
          type="button"
          onClick={onMarkReviewed}
          disabled={!canMarkReviewed}
        >
          {markLabel}
        </button>
        <button
          type="button"
          onClick={() => onSelect(index - 1)}
          disabled={busy || index === 0}
        >
          Previous
        </button>
        <button
          type="button"
          className="primary-action"
          onClick={() => onSelect(index + 1)}
          disabled={busy || !reviewed || index === manifest.items.length - 1}
        >
          {busy ? "Loading" : "Next image"}
        </button>
      </div>
      {error && <p className="gap-review-error" role="alert">{error}</p>}
    </section>
  );
}
