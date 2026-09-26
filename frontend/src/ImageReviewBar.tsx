import type { SaveStatus } from "./canvas/useAnnotationAutosave";
import { reviewButtonState } from "./imageReview";

type ImageReviewBarProps = {
  busy: boolean;
  reviewed: boolean;
  unresolvedCount: number;
  saveStatus: SaveStatus;
  error: string | null;
  place?: { position: number; total: number } | null;
  onPrevious?: () => void;
  onNext?: () => void;
  onMarkReviewed: () => void;
};

export function ImageReviewBar({
  busy,
  reviewed,
  unresolvedCount,
  saveStatus,
  error,
  place,
  onPrevious,
  onNext,
  onMarkReviewed,
}: ImageReviewBarProps) {
  const review = reviewButtonState(
    { reviewed, busy, unresolvedCount, saveStatus },
    onNext ? "Mark reviewed and next" : "Mark reviewed",
  );
  return (
    <section className="image-review-bar" aria-label="Image review">
      <p>
        {reviewed
          ? "Reviewed. Changing a box reopens it."
          : "Decide every box, then mark the photo reviewed."}
      </p>
      {place && (
        <div className="image-review-nav">
          <button type="button" onClick={onPrevious} disabled={busy || !onPrevious}>
            Previous
          </button>
          <span aria-label="Photo position">
            {place.position} of {place.total}
          </span>
          <button type="button" onClick={onNext} disabled={busy || !onNext}>
            Next
          </button>
        </div>
      )}
      <button
        type="button"
        className="primary-action"
        onClick={onMarkReviewed}
        disabled={!review.enabled}
      >
        {review.label}
      </button>
      {error && <p className="image-review-error" role="alert">{error}</p>}
    </section>
  );
}
