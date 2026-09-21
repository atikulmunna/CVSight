import { useEffect, useState } from "react";

import { AnnotationWorkspace } from "../canvas/AnnotationWorkspace";
import { loadLiveFixture } from "../canvas/liveFixture";
import type { CanvasFixture } from "../canvas/model";
import {
  loadReviewQueue,
  ReviewApiError,
  signOffReview,
  submitReviewDecision,
  type ReviewItem,
  type ReviewQueue,
} from "./api";

type ReviewWorkspaceProps = {
  datasetVersionId: string | null;
};

export function ReviewWorkspace({ datasetVersionId }: ReviewWorkspaceProps) {
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [selected, setSelected] = useState<ReviewItem | null>(null);
  const [fixture, setFixture] = useState<CanvasFixture | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function refreshQueue(
    preferredAnnotationId: string | null = selected?.annotationId ?? null,
  ) {
    if (!datasetVersionId) {
      return;
    }
    try {
      setBusy(true);
      const loaded = await loadReviewQueue(datasetVersionId);
      setQueue(loaded);
      setError(null);
      const next =
        loaded.items.find((item) => item.annotationId === preferredAnnotationId) ??
        loaded.items[0] ??
        null;
      setSelected(next);
      setFixture(next ? await loadLiveFixture(next.imageId) : null);
    } catch (requestError) {
      setError(reviewErrorMessage(requestError));
    } finally {
      setBusy(false);
    }
  }

  useEffect(() => {
    if (!datasetVersionId) {
      return;
    }
    let active = true;
    async function loadInitialQueue() {
      try {
        const loaded = await loadReviewQueue(datasetVersionId!);
        const first = loaded.items[0] ?? null;
        const loadedFixture = first ? await loadLiveFixture(first.imageId) : null;
        if (active) {
          setQueue(loaded);
          setSelected(first);
          setFixture(loadedFixture);
          setError(null);
        }
      } catch (requestError) {
        if (active) {
          setError(reviewErrorMessage(requestError));
        }
      }
    }
    void loadInitialQueue();
    return () => {
      active = false;
    };
  }, [datasetVersionId]);

  async function selectItem(item: ReviewItem) {
    try {
      setSelected(item);
      setBusy(true);
      setFixture(await loadLiveFixture(item.imageId));
      setError(null);
    } catch {
      setError("Review image unavailable");
    } finally {
      setBusy(false);
    }
  }

  async function decide(decision: "approve" | "flag") {
    if (!datasetVersionId || !selected) {
      return;
    }
    try {
      setBusy(true);
      await submitReviewDecision(datasetVersionId, selected, decision);
      setSelected(null);
      await refreshQueue(null);
    } catch {
      setError("Review item changed. Refresh and try again.");
      setBusy(false);
    }
  }

  async function signOff() {
    if (!datasetVersionId || !queue || queue.unresolvedCount > 0) {
      return;
    }
    try {
      setBusy(true);
      await signOffReview(datasetVersionId);
      await refreshQueue();
    } catch {
      setError("Snapshot sign-off failed");
      setBusy(false);
    }
  }

  if (!datasetVersionId) {
    return (
      <section className="review-empty">
        <h1>Open a dataset review</h1>
        <p>Add a dataset version to the URL with <code>?version=&lt;uuid&gt;</code>.</p>
      </section>
    );
  }

  return (
    <section className="review-workspace">
      <aside className="review-panel">
        <div className="review-heading">
          <h2>Quality review</h2>
          <span className="review-count">{queue?.unresolvedCount ?? 0}</span>
        </div>
        <p className="review-guidance">
          Correct boxes in the canvas, then refresh. Approve or flag the exact saved
          revision.
        </p>
        <div className="review-toolbar">
          <button type="button" onClick={() => void refreshQueue()} disabled={busy}>
            Refresh queue
          </button>
          <button
            type="button"
            className="primary-action"
            onClick={() => void signOff()}
            disabled={busy || !queue || queue.unresolvedCount > 0 || queue.status === "signed"}
          >
            {queue?.status === "signed" ? "Snapshot signed" : "Sign off snapshot"}
          </button>
        </div>
        {error && <p className="review-error">{error}</p>}
        <div className="review-list">
          {queue?.items.map((item, index) => (
            <button
              type="button"
              className={`review-item ${selected?.annotationId === item.annotationId ? "is-selected" : ""}`}
              key={item.annotationId}
              onClick={() => void selectItem(item)}
            >
              <span className="review-rank">{index + 1}</span>
              <span className="review-item-copy">
                <strong>{item.imageName}</strong>
                <span>Risk score {item.riskScore}</span>
                <span className="review-reasons">
                  {item.riskReasons.map((reason) => (
                    <span key={reason.code}>{reason.label}</span>
                  ))}
                </span>
              </span>
            </button>
          ))}
          {queue && queue.items.length === 0 && (
            <p className="review-cleared">
              {queue.status === "signed" ? "Snapshot review is signed." : "No blocking review items remain."}
            </p>
          )}
        </div>
        {selected && (
          <div className="review-actions">
            <button type="button" onClick={() => void decide("flag")} disabled={busy}>
              Flag
            </button>
            <button
              type="button"
              className="primary-action"
              onClick={() => void decide("approve")}
              disabled={busy}
            >
              Approve revision
            </button>
          </div>
        )}
      </aside>
      <div className="review-canvas">
        {fixture ? (
          <AnnotationWorkspace
            fixture={fixture}
            mode="verify"
            key={`${fixture.images[0]?.id ?? "review"}:${selected?.annotationRevision ?? 0}`}
          />
        ) : (
          <div className="review-canvas-empty">
            {busy ? "Loading review item..." : "Select a review item"}
          </div>
        )}
      </div>
    </section>
  );
}

function reviewErrorMessage(error: unknown): string {
  if (!(error instanceof ReviewApiError)) {
    return "Review queue unavailable";
  }
  if (error.code === "invalid_id") {
    return "The dataset version in the URL is invalid.";
  }
  if (error.code === "dataset_version_not_found") {
    return "That dataset version does not exist in the local database.";
  }
  if (error.code === "review_version_frozen") {
    return "That snapshot was frozen before QA sign-off and cannot be reviewed here.";
  }
  return "Review queue unavailable";
}
