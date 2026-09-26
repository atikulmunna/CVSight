import { useEffect, useState, type ReactNode } from "react";

import type { AuthSession } from "../auth/api";
import { isMissingProject, loadProject, type ProjectSummary } from "./api";
import {
  loadModelCandidates,
  loadModelDeployment,
  MODEL_ROLES,
  ModelsApiError,
  promoteModel,
  rollbackModel,
  type ModelDeployment,
  type ModelEntry,
  type ModelRole,
} from "./modelsApi";
import { ProjectBand } from "./ProjectBand";
import { ProjectsHeader } from "./ProjectsHeader";

type ProjectModelsPageProps = {
  projectId: string;
  currentUser: AuthSession;
  onBack: () => void;
  onOpenOverview: () => void;
  onOpenImages: () => void;
  onOpenReview: (project: ProjectSummary) => void;
  onOpenVersions: (project: ProjectSummary) => void;
  onOpenAnalytics: (project: ProjectSummary) => void;
  onLogout: () => Promise<void>;
};

type RegistryView = {
  candidates: ModelEntry[];
  deployment: ModelDeployment | null;
};

type PendingAction =
  | { kind: "promote"; entryId: string }
  | { kind: "rollback" }
  | null;

const ROLE_LABELS: Record<ModelRole, string> = {
  known_sku_detector: "Product detector",
  box_refiner: "Box refiner",
  recognition_embedder: "Recognition embedder",
  propagation_embedder: "Propagation embedder",
};

const METRIC_LABELS: Record<string, string> = {
  map_50_95: "mAP 50 to 95",
  product_recall_at_iou_50: "Product recall",
  duplicate_rate_at_iou_50: "Duplicate rate",
  dense_scene_recall_at_iou_50: "Dense-scene recall",
  overlapping_product_recall_at_iou_50: "Overlapping recall",
};

export function ProjectModelsPage({
  projectId,
  currentUser,
  onBack,
  onOpenOverview,
  onOpenImages,
  onOpenReview,
  onOpenVersions,
  onOpenAnalytics,
  onLogout,
}: ProjectModelsPageProps) {
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [projectStatus, setProjectStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [role, setRole] = useState<ModelRole>("known_sku_detector");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    loadProject(projectId)
      .then((selected) => {
        if (!active) {
          return;
        }
        setProject(selected);
        setProjectStatus("ready");
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }
        setProject(null);
        setProjectStatus(isMissingProject(error) ? "missing" : "error");
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  if (projectStatus !== "ready" || !project) {
    return (
      <div className="projects-shell">
        <ProjectsHeader currentUser={currentUser} section="Models" onLogout={onLogout} />
        <main className="project-versions-main">
          <section className="projects-message">
            <h1>
              {projectStatus === "loading"
                ? "Loading models"
                : projectStatus === "missing"
                  ? "Project not found"
                  : "Project unavailable"}
            </h1>
            <p>
              {projectStatus === "loading"
                ? "Reading the project and the model registry."
                : projectStatus === "missing"
                  ? "This project does not exist or is no longer available."
                  : "The project service could not be reached."}
            </p>
            {projectStatus !== "loading" && (
              <button type="button" onClick={onBack}>Back to projects</button>
            )}
          </section>
        </main>
      </div>
    );
  }

  return (
    <div className="projects-shell">
      <ProjectsHeader currentUser={currentUser} section="Models" onLogout={onLogout} />
      <main className="project-versions-main">
        <ProjectBand
          project={project}
          currentUser={currentUser}
          active="models"
          onBack={onBack}
          onOverview={onOpenOverview}
          onImages={onOpenImages}
          onReview={() => onOpenReview(project)}
          onVersions={() => onOpenVersions(project)}
          onModels={() => undefined}
          onAnalytics={() => onOpenAnalytics(project)}
        />

        <section className="project-versions-heading">
          <div>
            <h2>Models</h2>
            <p>
              Evaluated candidates and the active deployment for each model role. The
              registry is shared by every project.
            </p>
          </div>
        </section>

        <div className="models-roles" role="tablist" aria-label="Model roles">
          {MODEL_ROLES.map((option) => (
            <button
              key={option}
              type="button"
              role="tab"
              aria-selected={option === role}
              className={option === role ? "is-active" : ""}
              onClick={() => setRole(option)}
            >
              {ROLE_LABELS[option]}
            </button>
          ))}
        </div>

        <RoleRegistry key={`${role}:${refreshKey}`} role={role} onRetry={() => setRefreshKey((value) => value + 1)} />
      </main>
    </div>
  );
}

// Keyed by role, so switching roles or retrying starts from a clean state.
function RoleRegistry({ role, onRetry }: { role: ModelRole; onRetry: () => void }) {
  const [registry, setRegistry] = useState<RegistryView | null>(null);
  const [registryStatus, setRegistryStatus] = useState<"loading" | "ready" | "error">("loading");
  const [pending, setPending] = useState<PendingAction>(null);
  const [busy, setBusy] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    Promise.all([loadModelCandidates(role), loadModelDeployment(role)])
      .then(([candidates, deployment]) => {
        if (active) {
          setRegistry({ candidates, deployment });
          setRegistryStatus("ready");
        }
      })
      .catch(() => {
        if (active) {
          setRegistryStatus("error");
        }
      });
    return () => {
      active = false;
    };
  }, [role]);

  async function runPending() {
    if (!pending || !registry) {
      return;
    }
    setBusy(true);
    setActionError(null);
    try {
      const activeId = registry.deployment?.active.id ?? null;
      const deployment = pending.kind === "promote"
        ? await promoteModel(role, pending.entryId, activeId)
        : await rollbackModel(role, activeId ?? "");
      setRegistry({
        deployment,
        candidates: await loadModelCandidates(role),
      });
      setPending(null);
    } catch (error) {
      setActionError(actionErrorMessage(error));
    } finally {
      setBusy(false);
    }
  }

  const deployment = registry?.deployment ?? null;

  return (
    <>
      {registryStatus === "loading" ? (
        <p className="models-message">Loading the {ROLE_LABELS[role].toLowerCase()} registry...</p>
      ) : registryStatus === "error" || !registry ? (
        <div className="project-progress-error">
          <div>
            <strong>Registry unavailable</strong>
            <p>The model registry could not be reached.</p>
          </div>
          <button type="button" onClick={onRetry}>Try again</button>
        </div>
      ) : (
        <>
          <section className="versions-working-section" aria-labelledby="active-model-title">
            <div className="versions-section-heading">
              <h2 id="active-model-title">Active deployment</h2>
              <p>Workers load the promoted checkpoint after a restart.</p>
            </div>
            {deployment ? (
              <article className="working-version-card models-active-card">
                <ModelSummary entry={deployment.active} badge="Active" />
                <p className="models-deployment-note">
                  Promoted by {deployment.updatedBy} on {formatDate(deployment.updatedAt)}.
                  {deployment.previous
                    ? ` Rollback returns to ${deployment.previous.modelId} ${deployment.previous.modelVersion}.`
                    : " No previous deployment to roll back to."}
                </p>
                {deployment.previous && (
                  <ActionRow
                    label="Roll back"
                    confirmLabel={`Confirm rollback to ${deployment.previous.modelVersion}`}
                    pending={pending?.kind === "rollback"}
                    busy={busy}
                    onRequest={() => setPending({ kind: "rollback" })}
                    onCancel={() => setPending(null)}
                    onConfirm={() => void runPending()}
                  />
                )}
              </article>
            ) : (
              <div className="working-version-empty">
                <div>
                  <strong>No model promoted for this role</strong>
                  <p>Promote a registered candidate below. Until then, workers use their operator-configured checkpoint.</p>
                </div>
              </div>
            )}
          </section>

          <section className="versions-release-section" aria-labelledby="candidates-title">
            <div className="versions-section-heading">
              <h2 id="candidates-title">Candidates</h2>
              <p>Registered with a frozen evaluation. Newer candidates never replace the default on their own.</p>
            </div>
            {actionError && (
              <div className="create-project-error" role="alert">{actionError}</div>
            )}
            {registry.candidates.length === 0 ? (
              <div className="versions-release-empty">
                <strong>No candidates registered</strong>
                <p>Train from a signed snapshot export, evaluate on its frozen test split, then register the checkpoint and report with the model registry API.</p>
              </div>
            ) : (
              <div className="version-release-list">
                {registry.candidates.map((entry) => (
                  <article key={entry.id} className="version-release-card">
                    <ModelSummary entry={entry} badge={statusLabel(entry)} />
                    {entry.deploymentStatus !== "default" && (
                      <ActionRow
                        label="Promote"
                        confirmLabel={`Confirm promote ${entry.modelVersion}`}
                        pending={pending?.kind === "promote" && pending.entryId === entry.id}
                        busy={busy}
                        onRequest={() => setPending({ kind: "promote", entryId: entry.id })}
                        onCancel={() => setPending(null)}
                        onConfirm={() => void runPending()}
                      />
                    )}
                  </article>
                ))}
              </div>
            )}
          </section>
        </>
      )}
    </>
  );
}

function ModelSummary({ entry, badge }: { entry: ModelEntry; badge: string }) {
  return (
    <>
      <div className="version-card-title">
        <div>
          <span className={`version-status ${entry.deploymentStatus === "default" ? "working" : entry.deploymentStatus === "previous" ? "frozen" : ""}`}>
            {badge}
          </span>
          <h3>{entry.modelId} {entry.modelVersion}</h3>
        </div>
        <code title={entry.modelArtifactSha256}>{entry.modelArtifactSha256.slice(0, 12)}</code>
      </div>
      <dl className="models-metrics">
        {Object.entries(entry.metrics).map(([key, value]) => (
          <div key={key}>
            <dt>{METRIC_LABELS[key] ?? key}</dt>
            <dd>{formatMetric(value)}</dd>
          </div>
        ))}
      </dl>
      <p className="release-review-note">
        Registered by {entry.registeredBy} on {formatDate(entry.registeredAt)}.{" "}
        {entry.trainingDatasetVersionId
          ? `Trained on version ${entry.trainingDatasetVersionId.slice(0, 8)}`
          : `Trained outside CVSight (${entry.source})`}
        , evaluated on version {entry.evaluationDatasetVersionId.slice(0, 8)}.
      </p>
    </>
  );
}

function ActionRow({
  label,
  confirmLabel,
  pending,
  busy,
  onRequest,
  onCancel,
  onConfirm,
}: {
  label: string;
  confirmLabel: string;
  pending: boolean;
  busy: boolean;
  onRequest: () => void;
  onCancel: () => void;
  onConfirm: () => void;
}): ReactNode {
  if (!pending) {
    return (
      <div className="release-export-actions">
        <button type="button" disabled={busy} onClick={onRequest}>{label}</button>
      </div>
    );
  }
  return (
    <div className="release-export-actions">
      <button className="projects-primary-action" type="button" disabled={busy} onClick={onConfirm}>
        {busy ? "Working..." : confirmLabel}
      </button>
      <button type="button" disabled={busy} onClick={onCancel}>Cancel</button>
    </div>
  );
}

function statusLabel(entry: ModelEntry): string {
  if (entry.deploymentStatus === "default") {
    return "Active";
  }
  if (entry.deploymentStatus === "previous") {
    return "Previous";
  }
  return "Candidate";
}

function actionErrorMessage(error: unknown): string {
  if (error instanceof ModelsApiError) {
    if (error.code === "stale_model_deployment") {
      return "The deployment changed since this page loaded. Refresh and try again.";
    }
    if (error.code === "model_incompatible") {
      return "This candidate is not compatible with the current runtime.";
    }
    if (error.code === "model_rollback_unavailable") {
      return "There is no previous deployment to roll back to.";
    }
  }
  return "The deployment could not be changed. Try again.";
}

function formatMetric(value: number): string {
  if (value >= 0 && value <= 1) {
    return `${(value * 100).toFixed(1)}%`;
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}
