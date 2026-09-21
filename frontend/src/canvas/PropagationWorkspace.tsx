import { useMemo, useState } from "react";

import { AnnotationOverlay } from "./AnnotationOverlay";
import type { AnnotationBox, CanvasFixture } from "./model";
import {
  confirmPropagationSuggestions,
  createPropagationSuggestions,
  PropagationApiError,
  queuePropagationEmbeddings,
  type PropagationCandidate,
  type PropagationSuggestionSet,
} from "./propagationApi";

type PropagationWorkspaceProps = {
  fixture: CanvasFixture;
  annotations: AnnotationBox[];
  onAnnotationsChange: (annotations: AnnotationBox[]) => void;
};

const UNKNOWN_SKU_ID = "00000000-0000-0000-0000-000000000001";

export function PropagationWorkspace({
  fixture,
  annotations,
  onAnnotationsChange,
}: PropagationWorkspaceProps) {
  const seeds = useMemo(
    () =>
      annotations.filter(
        (annotation) =>
          annotation.serverId !== null &&
          annotation.revision !== null &&
          annotation.classType === "product" &&
          annotation.lifecycleState === "verified" &&
          annotation.reviewState === "accepted" &&
          annotation.skuId !== null &&
          annotation.skuId !== UNKNOWN_SKU_ID,
      ),
    [annotations],
  );
  const eligibleEmbeddings = useMemo(
    () =>
      annotations.filter(
        (annotation) =>
          annotation.serverId !== null &&
          annotation.classType === "product" &&
          annotation.lifecycleState === "verified" &&
          annotation.reviewState === "accepted" &&
          annotation.skuId !== UNKNOWN_SKU_ID,
      ),
    [annotations],
  );
  const [seedId, setSeedId] = useState<string | null>(seeds[0]?.id ?? null);
  const [suggestionSet, setSuggestionSet] =
    useState<PropagationSuggestionSet | null>(null);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [reviewedIds, setReviewedIds] = useState<Set<string>>(new Set());
  const [message, setMessage] = useState(
    "Choose a human-assigned seed, then find visually similar unlabeled crops.",
  );
  const [busy, setBusy] = useState(false);
  const [completed, setCompleted] = useState<{
    selected: number;
    skipped: number;
  } | null>(null);

  const seed = seeds.find((annotation) => annotation.id === seedId) ?? null;
  const selectedCount = selectedIds.size;
  const skippedCount =
    (suggestionSet?.candidates.length ?? 0) - selectedCount;

  async function queueEmbeddings() {
    if (eligibleEmbeddings.length === 0 || busy) {
      return;
    }
    setBusy(true);
    try {
      const result = await queuePropagationEmbeddings(eligibleEmbeddings);
      setMessage(
        `${result.queued} crop embedding jobs queued or already present; ${result.rejected} rejected.`,
      );
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  async function findSimilar() {
    if (!seed || busy) {
      return;
    }
    setBusy(true);
    setSuggestionSet(null);
    setSelectedIds(new Set());
    setReviewedIds(new Set());
    setCompleted(null);
    try {
      const result = await createPropagationSuggestions(seed);
      setSuggestionSet(result);
      setMessage(
        result.candidates.length === 0
          ? "No unlabeled crops are available for propagation."
          : "Review each crop. Nothing is selected automatically.",
      );
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  function toggleCandidate(candidate: PropagationCandidate) {
    setSelectedIds((current) => {
      const next = new Set(current);
      if (next.has(candidate.suggestionId)) {
        next.delete(candidate.suggestionId);
        setReviewedIds((reviewed) => {
          const updated = new Set(reviewed);
          updated.delete(candidate.suggestionId);
          return updated;
        });
      } else {
        next.add(candidate.suggestionId);
        if (candidate.requiresIndividualReview) {
          setReviewedIds((reviewed) =>
            new Set(reviewed).add(candidate.suggestionId),
          );
        }
      }
      return next;
    });
  }

  async function confirm() {
    if (!suggestionSet || suggestionSet.candidates.length === 0 || busy) {
      return;
    }
    setBusy(true);
    try {
      const result = await confirmPropagationSuggestions(
        suggestionSet,
        selectedIds,
        reviewedIds,
        annotations,
      );
      const savedById = new Map(
        result.annotations.map((annotation) => [annotation.id, annotation]),
      );
      onAnnotationsChange(
        annotations.map((annotation) => savedById.get(annotation.id) ?? annotation),
      );
      setCompleted({
        selected: result.selectedCount,
        skipped: result.skippedCount,
      });
      setMessage(
        `${result.selectedCount} confirmed; ${result.skippedCount} skipped.`,
      );
    } catch (error) {
      setMessage(errorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="propagation-workspace">
      <section className="propagation-seed-panel" aria-label="Propagation seed">
        <div className="panel-heading">
          <div>
            <span className="eyebrow">Seed facing</span>
            <strong>Choose one confirmed label</strong>
          </div>
          <span className="panel-count">{seeds.length}</span>
        </div>
        <label className="propagation-seed-picker">
          Confirmed SKU assignment
          <select
            value={seedId ?? ""}
            onChange={(event) => {
              setSeedId(event.target.value || null);
              setSuggestionSet(null);
              setCompleted(null);
            }}
          >
            {seeds.map((annotation) => (
              <option value={annotation.id} key={annotation.id}>
                {annotation.sku} · {annotation.id}
              </option>
            ))}
          </select>
        </label>
        <div
          className="propagation-seed-canvas"
          style={{ aspectRatio: `${fixture.width} / ${fixture.height}` }}
        >
          {fixture.images.map((image) => (
            <img
              src={image.url}
              alt=""
              style={{
                left: `${(image.x / fixture.width) * 100}%`,
                top: `${(image.y / fixture.height) * 100}%`,
                width: `${(image.width / fixture.width) * 100}%`,
                height: `${(image.height / fixture.height) * 100}%`,
              }}
              key={image.id}
            />
          ))}
          <AnnotationOverlay
            annotations={annotations}
            width={fixture.width}
            height={fixture.height}
            selectedId={seedId}
            interactive
            onSelect={(annotationId) => {
              if (seeds.some((candidate) => candidate.id === annotationId)) {
                setSeedId(annotationId);
                setSuggestionSet(null);
                setCompleted(null);
              }
            }}
          />
        </div>
        <div className="propagation-seed-actions">
          <button
            type="button"
            onClick={() => void queueEmbeddings()}
            disabled={busy || eligibleEmbeddings.length === 0}
          >
            Queue crop embeddings
          </button>
          <button
            type="button"
            className="primary-action"
            onClick={() => void findSimilar()}
            disabled={busy || !seed}
          >
            Find similar crops
          </button>
        </div>
      </section>

      <section className="propagation-review-panel" aria-label="Propagation review">
        <header className="propagation-review-header">
          <div>
            <span className="eyebrow">Confirmation grid</span>
            <strong>
              {suggestionSet
                ? suggestionSet.seedSku.name
                : "No suggestion set loaded"}
            </strong>
          </div>
          {suggestionSet && (
            <span className="model-evidence">
              Exploratory model · {suggestionSet.qualityEvidence}
            </span>
          )}
        </header>
        {suggestionSet?.hardPairs.length ? (
          <div className="hard-pair-warning" role="alert">
            <strong>Fine-grained confusion risk</strong>
            <span>
              Individual review required against{" "}
              {suggestionSet.hardPairs
                .map((pair) => `${pair.name}: ${pair.reason}`)
                .join(", ")}
            </span>
          </div>
        ) : null}
        <p className="propagation-message" role="status">
          {message}
        </p>
        <div className="propagation-grid">
          {suggestionSet?.candidates.map((candidate) => (
            <CandidateCard
              candidate={candidate}
              selected={selectedIds.has(candidate.suggestionId)}
              onToggle={() => toggleCandidate(candidate)}
              key={candidate.suggestionId}
            />
          ))}
        </div>
        <footer className="propagation-confirm-bar">
          <span>
            <strong>{selectedCount} selected</strong> · {skippedCount} skipped
          </span>
          {completed ? (
            <strong>
              Saved {completed.selected}; skipped {completed.skipped}
            </strong>
          ) : (
            <button
              type="button"
              className="primary-action"
              onClick={() => void confirm()}
              disabled={
                busy ||
                !suggestionSet ||
                suggestionSet.candidates.length === 0
              }
            >
              Confirm {selectedCount} · skip {skippedCount}
            </button>
          )}
        </footer>
      </section>
    </main>
  );
}

function CandidateCard({
  candidate,
  selected,
  onToggle,
}: {
  candidate: PropagationCandidate;
  selected: boolean;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      className={`propagation-card${selected ? " is-selected" : ""}${
        candidate.requiresIndividualReview ? " needs-review" : ""
      }`}
      aria-pressed={selected}
      onClick={onToggle}
    >
      <span className="propagation-crop">
        <img
          src={candidate.imageUrl}
          alt=""
          style={{
            width: `${(candidate.imageWidth / candidate.width) * 100}%`,
            height: `${(candidate.imageHeight / candidate.height) * 100}%`,
            left: `${(-candidate.x / candidate.width) * 100}%`,
            top: `${(-candidate.y / candidate.height) * 100}%`,
          }}
        />
      </span>
      <span className="propagation-card-meta">
        <strong>{Math.round(candidate.score * 100)}%</strong>
        <span>{selected ? "Selected" : "Skipped"}</span>
      </span>
      {candidate.requiresIndividualReview && (
        <span className="confusion-badge">Review variant</span>
      )}
    </button>
  );
}

function errorMessage(error: unknown): string {
  if (!(error instanceof PropagationApiError)) {
    return "Propagation is unavailable. Try again.";
  }
  const messages: Record<string, string> = {
    embedding_not_ready:
      "The seed embedding is not ready. Queue crops and let the worker finish.",
    propagation_index_not_current:
      "Some unlabeled crops are not embedded yet. Queue crops and let the worker finish.",
    invalid_propagation_seed:
      "Choose a product with a human-confirmed known SKU assignment.",
    stale_revision:
      "A crop changed during review. Create a fresh suggestion set.",
    invalid_propagation_decision:
      "The batch could not be saved. Review every flagged crop and retry.",
  };
  return messages[error.code] ?? "Propagation is unavailable. Try again.";
}
