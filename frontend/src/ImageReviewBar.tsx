import type { SaveStatus } from "./canvas/useAnnotationAutosave";
import { reviewButtonState } from "./imageReview";

type ImageReviewBarProps = {
  busy: boolean;
  reviewed: boolean;
  unresolvedCount: number;
  saveStatus: SaveStatus;
  error: string | null;
  onMarkReviewed: () => void;
};

export function ImageReviewBar({
  busy,
  reviewed,
  unresolvedCount,
  saveStatus,
  error,
  onMarkReviewed,
}: ImageReviewBarProps) {
  const review = reviewButtonState({ reviewed, busy, unresolvedCount, saveStatus });
  return (
    <section className="image-review-bar" aria-label="Image review">
      <p>
        {reviewed
          ? "Reviewed. Changing a box reopens it."
          : "Decide every box, then mark the photo reviewed."}
      </p>
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
