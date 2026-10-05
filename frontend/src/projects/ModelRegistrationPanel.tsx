import { useEffect, useState } from "react";

import { formatMetric, metricLabel } from "./modelMetrics";
import {
  loadEvaluationDefaults,
  loadModelEvaluation,
  ModelsApiError,
  registerEvaluatedModel,
  startModelEvaluation,
  type EvaluationReport,
  type ModelEntry,
  type ModelEvaluation,
} from "./modelsApi";
import { loadProjectVersions, type ProjectVersion } from "./versionsApi";

type Stage =
  | { kind: "form" }
  | { kind: "evaluating"; evaluation: ModelEvaluation }
  | { kind: "failed"; message: string }
  | { kind: "evaluated"; evaluationId: string; report: EvaluationReport }
  | { kind: "registered"; entry: ModelEntry };

type Release = { id: string; label: string };

const SCORES = [
  "map_50",
  "map_50_95",
  "product_recall_at_iou_50",
  "precision_at_iou_50",
  "duplicate_rate_at_iou_50",
  "dense_scene_recall_at_iou_50",
  "overlapping_product_recall_at_iou_50",
];

export function ModelRegistrationPanel({
  projectId,
  onRegistered,
  pollInterval = 2000,
}: {
  projectId: string;
  onRegistered: () => void;
  pollInterval?: number;
}) {
  const [releases, setReleases] = useState<Release[] | null>(null);
  const [versionId, setVersionId] = useState("");
  const [runtimeUrl, setRuntimeUrl] = useState("");
  const [threshold, setThreshold] = useState(0.3);
  const [stage, setStage] = useState<Stage>({ kind: "form" });
  const [source, setSource] = useState("");
  const [licensed, setLicensed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    loadProjectVersions(projectId)
      .then((versions) => {
        if (active) {
          const released = releaseOptions(versions);
          setReleases(released);
          setVersionId((current) => current || released[0]?.id || "");
        }
      })
      .catch(() => {
        if (active) {
          setReleases([]);
        }
      });
    loadEvaluationDefaults()
      .then((url) => {
        if (active && url) {
          setRuntimeUrl((current) => current || url);
        }
      })
      .catch(() => undefined);
    // An evaluation keeps running in the worker, so a reload picks it up again.
    const storedId = readStoredEvaluation(projectId);
    if (storedId) {
      loadModelEvaluation(storedId)
        .then((evaluation) => {
          if (active) {
            setStage(stageFor(evaluation));
          }
        })
        .catch(() => forgetEvaluation(projectId));
    }
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (stage.kind !== "evaluating") {
      return;
    }
    const timer = window.setTimeout(() => {
      loadModelEvaluation(stage.evaluation.id)
        .then((evaluation) => setStage(stageFor(evaluation)))
        .catch(() => setStage({ ...stage }));
    }, pollInterval);
    return () => window.clearTimeout(timer);
  }, [stage, pollInterval]);

  async function runEvaluation() {
    setBusy(true);
    setError(null);
    try {
      const evaluation = await startModelEvaluation({
        datasetVersionId: versionId,
        runtimeUrl: runtimeUrl.trim(),
        confidenceThreshold: threshold,
      });
      rememberEvaluation(projectId, evaluation.id);
      setStage(stageFor(evaluation));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  async function register(evaluationId: string) {
    setBusy(true);
    setError(null);
    try {
      const entry = await registerEvaluatedModel(evaluationId, source.trim(), licensed);
      forgetEvaluation(projectId);
      setStage({ kind: "registered", entry });
      onRegistered();
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setBusy(false);
    }
  }

  function startOver() {
    forgetEvaluation(projectId);
    setStage({ kind: "form" });
    setError(null);
    setSource("");
    setLicensed(false);
  }

  return (
    <section className="model-registration" aria-labelledby="model-registration-title">
      <div className="model-registration-heading">
        <h3 id="model-registration-title">Register a model</h3>
        <p>
          Test a model on a signed-off release it has not seen, review its scores, then add it
          as a candidate. Promoting it stays a separate step.
        </p>
      </div>

      {stage.kind === "form" && (
        <EvaluationForm
          releases={releases}
          versionId={versionId}
          runtimeUrl={runtimeUrl}
          threshold={threshold}
          busy={busy}
          onVersion={setVersionId}
          onRuntimeUrl={setRuntimeUrl}
          onThreshold={setThreshold}
          onSubmit={() => void runEvaluation()}
        />
      )}

      {stage.kind === "evaluating" && (
        <p className="model-registration-progress" role="status">
          {progressText(stage.evaluation)}
        </p>
      )}

      {stage.kind === "failed" && (
        <div className="model-registration-result">
          <p className="model-registration-error" role="alert">{stage.message}</p>
          <button type="button" onClick={startOver}>Start over</button>
        </div>
      )}

      {stage.kind === "evaluated" && (
        <div className="model-registration-result">
          <EvaluationScores report={stage.report} />
          <label className="model-registration-source">
            Where does this model come from?
            <textarea
              value={source}
              maxLength={500}
              placeholder="For example: two-stage shelf detector trained by the retail CV team on 2026 captures"
              onChange={(event) => setSource(event.target.value)}
            />
          </label>
          <label className="model-registration-check">
            <input
              type="checkbox"
              checked={licensed}
              onChange={(event) => setLicensed(event.target.checked)}
            />
            Its license allows us to use it here
          </label>
          <div className="model-registration-actions">
            <button
              type="button"
              className="primary-action"
              disabled={busy || !source.trim() || !licensed}
              onClick={() => void register(stage.evaluationId)}
            >
              Register model
            </button>
            <button type="button" onClick={startOver}>Discard</button>
          </div>
        </div>
      )}

      {stage.kind === "registered" && (
        <div className="model-registration-result">
          <p role="status">
            Registered {stage.entry.modelId} {stage.entry.modelVersion} as a candidate. Promote
            it below when you are ready to pre-label with it.
          </p>
          <button type="button" onClick={startOver}>Register another model</button>
        </div>
      )}

      {error && <p className="model-registration-error" role="alert">{error}</p>}
    </section>
  );
}

function EvaluationForm({
  releases,
  versionId,
  runtimeUrl,
  threshold,
  busy,
  onVersion,
  onRuntimeUrl,
  onThreshold,
  onSubmit,
}: {
  releases: Release[] | null;
  versionId: string;
  runtimeUrl: string;
  threshold: number;
  busy: boolean;
  onVersion: (value: string) => void;
  onRuntimeUrl: (value: string) => void;
  onThreshold: (value: number) => void;
  onSubmit: () => void;
}) {
  if (releases !== null && releases.length === 0) {
    return (
      <p className="model-registration-note">
        Sign off a version first. A model is tested on a frozen release so its scores never
        change afterwards.
      </p>
    );
  }
  const thresholdValid = Number.isFinite(threshold) && threshold >= 0 && threshold <= 1;
  return (
    <div className="model-registration-form">
      <label>
        Model service address
        <input
          type="url"
          value={runtimeUrl}
          placeholder="http://host.docker.internal:8091"
          onChange={(event) => onRuntimeUrl(event.target.value)}
        />
      </label>
      <label>
        Test on release
        <select value={versionId} onChange={(event) => onVersion(event.target.value)}>
          {(releases ?? []).map((release) => (
            <option key={release.id} value={release.id}>
              {release.label}
            </option>
          ))}
        </select>
      </label>
      <label>
        Confidence threshold
        <input
          type="number"
          min={0}
          max={1}
          step={0.05}
          value={Number.isNaN(threshold) ? "" : threshold}
          onChange={(event) => onThreshold(event.target.valueAsNumber)}
        />
      </label>
      <button
        type="button"
        className="primary-action"
        disabled={busy || !runtimeUrl.trim() || !versionId || !thresholdValid}
        onClick={onSubmit}
      >
        Run evaluation
      </button>
      <p className="model-registration-note">
        The worker sends every photo in the release to the model and scores its boxes against
        your verified products. Pick a release the model was not trained on, and ideally one
        labeled without its pre-labels, so the scores are not flattered.
      </p>
    </div>
  );
}

function EvaluationScores({ report }: { report: EvaluationReport }) {
  return (
    <>
      <p className="model-registration-identity">
        <strong>
          {report.modelId} {report.modelVersion}
        </strong>{" "}
        <code title={report.modelArtifactSha256}>{report.modelArtifactSha256.slice(0, 12)}</code>
        <span>
          {report.photos} photos, {report.groundTruthBoxes} verified products,{" "}
          {report.predictedBoxes} model boxes at confidence {report.confidenceThreshold}
        </span>
      </p>
      <dl className="models-metrics">
        {SCORES.filter((key) => key in report.metrics).map((key) => (
          <div key={key}>
            <dt>{metricLabel(key)}</dt>
            <dd>{formatMetric(key, report.metrics[key] ?? null)}</dd>
          </div>
        ))}
      </dl>
    </>
  );
}

function releaseOptions(versions: ProjectVersion[]): Release[] {
  // Numbered oldest first, matching the Versions page.
  const numbers = new Map([...versions].reverse().map((version, index) => [version.id, index + 1]));
  return versions
    .filter((version) => version.status !== "working")
    .map((version) => ({
      id: version.id,
      label: `Version ${numbers.get(version.id)} (${version.imageCount} photos)`,
    }));
}

function stageFor(evaluation: ModelEvaluation): Stage {
  if (evaluation.state === "succeeded" && evaluation.report) {
    return { kind: "evaluated", evaluationId: evaluation.id, report: evaluation.report };
  }
  if (evaluation.state === "failed" || evaluation.state === "cancelled") {
    return { kind: "failed", message: failureMessage(evaluation.errorCode) };
  }
  return { kind: "evaluating", evaluation };
}

function progressText(evaluation: ModelEvaluation): string {
  if (evaluation.state === "queued") {
    return "Waiting for the worker to start the evaluation.";
  }
  const total = evaluation.progressTotal ?? 0;
  return `Evaluating photo ${evaluation.progressCurrent} of ${total}.`;
}

function failureMessage(code: string | null): string {
  switch (code) {
    case "model_runtime_unavailable":
      return "The model service could not be reached at that address. Check that it is running.";
    case "no_verified_products":
      return "That release has no verified product boxes to score against.";
    case "model_changed":
      return "The model service switched models during the evaluation. Run it again.";
    default:
      return `The evaluation failed${code ? ` (${code})` : ""}. Start over to try again.`;
  }
}

function errorMessage(error: unknown): string {
  const code = error instanceof ModelsApiError ? error.code : null;
  switch (code) {
    case "invalid_runtime_url":
      return "Use a web address that starts with http:// or https://.";
    case "dataset_version_not_frozen":
      return "Pick a signed-off release.";
    case "model_registration_conflict":
      return "This model is already registered. Each model is registered once, with its evaluation.";
    case "invalid_model_lineage":
      return "The registry refused it: a required score is missing or the license is not approved.";
    case "evaluation_not_ready":
      return "The evaluation has not finished yet.";
    default:
      return "That did not work. Try again.";
  }
}

function storageKey(projectId: string): string {
  return `cvsight-model-evaluation:${projectId}`;
}

function readStoredEvaluation(projectId: string): string | null {
  try {
    return window.localStorage.getItem(storageKey(projectId));
  } catch {
    return null;
  }
}

function rememberEvaluation(projectId: string, evaluationId: string): void {
  try {
    window.localStorage.setItem(storageKey(projectId), evaluationId);
  } catch {
    // Without storage, a reload just shows the form again; the job still finishes.
  }
}

function forgetEvaluation(projectId: string): void {
  try {
    window.localStorage.removeItem(storageKey(projectId));
  } catch {
    // Nothing was stored, so there is nothing to clear.
  }
}
