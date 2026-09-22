import { useEffect, useState, type ReactNode } from "react";

import type { AuthSession } from "../auth/api";
import { isMissingProject, loadProject, type ProjectSummary } from "./api";
import { ProjectBand } from "./ProjectBand";
import { ProjectsHeader } from "./ProjectsHeader";
import {
  createWorkingVersion,
  loadProjectVersions,
  loadWorkingVersionReview,
  versionExportUrl,
  type ProjectVersion,
  type WorkingVersionReview,
} from "./versionsApi";

type ProjectVersionsPageProps = {
  projectId: string;
  currentUser: AuthSession;
  onBack: () => void;
  onOpenOverview: () => void;
  onOpenImages: () => void;
  onOpenReview: (project: ProjectSummary) => void;
  onOpenModels: (project: ProjectSummary) => void;
  onOpenAnalytics: (project: ProjectSummary) => void;
  onLogout: () => Promise<void>;
};

type VersionsView = {
  project: ProjectSummary;
  versions: ProjectVersion[];
  review: WorkingVersionReview | null;
  reviewAvailable: boolean;
};

export function ProjectVersionsPage({
  projectId,
  currentUser,
  onBack,
  onOpenOverview,
  onOpenImages,
  onOpenReview,
  onOpenModels,
  onOpenAnalytics,
  onLogout,
}: ProjectVersionsPageProps) {
  const [view, setView] = useState<VersionsView | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">("loading");
  const [refreshKey, setRefreshKey] = useState(0);
  const [creating, setCreating] = useState(false);
  const [createError, setCreateError] = useState(false);

  useEffect(() => {
    let active = true;
    Promise.all([loadProject(projectId), loadProjectVersions(projectId)])
      .then(async ([project, versions]) => {
        const working = versions.find((version) => version.status === "working") ?? null;
        let review: WorkingVersionReview | null = null;
        let reviewAvailable = working === null;
        if (working) {
          try {
            review = await loadWorkingVersionReview(working.id);
            reviewAvailable = true;
          } catch {
            reviewAvailable = false;
          }
        }
        if (active) {
          setView({ project, versions, review, reviewAvailable });
          setStatus("ready");
        }
      })
      .catch((error: unknown) => {
        if (active) {
          setStatus(isMissingProject(error) ? "missing" : "error");
        }
      });
    return () => {
      active = false;
    };
  }, [projectId, refreshKey]);

  async function startWorkingVersion() {
    if (!view || creating) {
      return;
    }
    setCreating(true);
    setCreateError(false);
    try {
      await createWorkingVersion(view.project.id);
      setStatus("loading");
      setRefreshKey((value) => value + 1);
    } catch {
      setCreateError(true);
    } finally {
      setCreating(false);
    }
  }

  return (
    <div className="projects-shell">
      <ProjectsHeader currentUser={currentUser} section="Versions and releases" onLogout={onLogout} />
      {status !== "ready" || !view ? (
        <VersionsMessage
          title={
            status === "loading"
              ? "Loading versions"
              : status === "missing"
                ? "Project not found"
                : "Versions unavailable"
          }
          detail={
            status === "loading"
              ? "Reading working and immutable project versions."
              : status === "missing"
                ? "This project does not exist or is no longer available."
                : "The version service could not be reached."
          }
          action={status === "loading" ? undefined : (
            <button type="button" onClick={status === "error" ? () => {
              setStatus("loading");
              setRefreshKey((value) => value + 1);
            } : onBack}>
              {status === "error" ? "Try again" : "Back to projects"}
            </button>
          )}
        />
      ) : (
        <VersionsContent
          view={view}
          currentUser={currentUser}
          creating={creating}
          createError={createError}
          onBack={onBack}
          onOpenOverview={onOpenOverview}
          onOpenImages={onOpenImages}
          onOpenReview={() => onOpenReview(view.project)}
          onOpenModels={() => onOpenModels(view.project)}
          onOpenAnalytics={() => onOpenAnalytics(view.project)}
          onStartWorkingVersion={() => void startWorkingVersion()}
        />
      )}
    </div>
  );
}

function VersionsContent({
  view,
  currentUser,
  creating,
  createError,
  onBack,
  onOpenOverview,
  onOpenImages,
  onOpenReview,
  onOpenModels,
  onOpenAnalytics,
  onStartWorkingVersion,
}: {
  view: VersionsView;
  currentUser: AuthSession;
  creating: boolean;
  createError: boolean;
  onBack: () => void;
  onOpenOverview: () => void;
  onOpenImages: () => void;
  onOpenReview: () => void;
  onOpenModels: () => void;
  onOpenAnalytics: () => void;
  onStartWorkingVersion: () => void;
}) {
  const working = view.versions.find((version) => version.status === "working") ?? null;
  const releases = view.versions.filter((version) => version.status !== "working");
  const versionNumbers = new Map(
    [...view.versions].reverse().map((version, index) => [version.id, index + 1]),
  );

  return (
    <main className="project-versions-main">
      <ProjectBand
        project={view.project}
        currentUser={currentUser}
        active="versions"
        status={
          <span className={`project-version-state ${working ? "open" : "frozen"}`}>
            {working
              ? "Working version open"
              : `${releases.length} immutable ${releases.length === 1 ? "release" : "releases"}`}
          </span>
        }
        onBack={onBack}
        onOverview={onOpenOverview}
        onImages={onOpenImages}
        onReview={onOpenReview}
        onVersions={() => undefined}
        onModels={onOpenModels}
        onAnalytics={onOpenAnalytics}
      />

      <section className="project-versions-heading">
        <div>
          <h2>Versions and releases</h2>
          <p>Keep active annotation work separate from signed, immutable datasets.</p>
        </div>
      </section>

      <section className="versions-working-section" aria-labelledby="working-version-title">
        <div className="versions-section-heading">
          <h2 id="working-version-title">Working version</h2>
          <p>Edits remain live until QA sign-off creates an immutable release.</p>
        </div>
        {working ? (
          <article className="working-version-card">
            <div className="version-card-title">
              <div>
                <span className="version-status working">Working</span>
                <h3>Version {versionNumbers.get(working.id)}</h3>
              </div>
              <code>{shortId(working.id)}</code>
            </div>
            <div className="working-version-facts">
              <VersionFact label="Created" value={formatDate(working.createdAt)} />
              <VersionFact label="Images" value={String(working.imageCount)} />
              <VersionFact
                label="Review status"
                value={reviewStatusLabel(view.review, view.reviewAvailable)}
              />
            </div>
            <div className="working-version-review">
              <p>{reviewStatusDetail(view.review, view.reviewAvailable)}</p>
              <button type="button" onClick={onOpenReview}>Open Review QA</button>
            </div>
          </article>
        ) : (
          <div className="working-version-empty">
            <div>
              <strong>No working version</strong>
              <p>Released data is locked. Start the next version to continue annotation.</p>
            </div>
            <button
              className="projects-primary-action"
              type="button"
              disabled={creating || releases.length === 0}
              onClick={onStartWorkingVersion}
            >
              {creating ? "Starting..." : "Start next working version"}
            </button>
          </div>
        )}
        {createError && (
          <div className="create-project-error" role="alert">
            The next working version could not be started. Refresh and try again.
          </div>
        )}
      </section>

      <section className="versions-release-section" aria-labelledby="release-history-title">
        <div className="versions-section-heading">
          <h2 id="release-history-title">Releases</h2>
          <p>Downloads always use the exact snapshot captured for that release.</p>
        </div>
        {releases.length === 0 ? (
          <div className="versions-release-empty">
            <strong>No releases yet</strong>
            <p>Complete Review QA and sign off the working version to create the first release.</p>
          </div>
        ) : (
          <div className="version-release-list">
            {releases.map((version) => (
              <ReleaseCard
                key={version.id}
                version={version}
                number={versionNumbers.get(version.id) ?? 1}
              />
            ))}
          </div>
        )}
      </section>
    </main>
  );
}

function ReleaseCard({ version, number }: { version: ProjectVersion; number: number }) {
  const signed = version.reviewSignoff;
  return (
    <article className="version-release-card">
      <div className="version-card-title">
        <div>
          <span className={`version-status ${version.status}`}>
            {version.status === "released" ? "Released" : "Frozen"}
          </span>
          <h3>Version {number}</h3>
        </div>
        <code>{shortId(version.id)}</code>
      </div>
      <div className="release-version-facts">
        <VersionFact label="Captured" value={formatDate(version.snapshotAt!)} />
        <VersionFact label="Images" value={String(version.imageCount)} />
        <VersionFact
          label="QA sign-off"
          value={signed ? `${signed.signedBy} · ${formatDate(signed.signedAt)}` : "Not recorded"}
        />
      </div>
      {signed ? (
        <p className="release-review-note">
          {signed.reviewedAnnotationCount} reviewed {signed.reviewedAnnotationCount === 1 ? "annotation" : "annotations"} · {signed.riskItemCount} {signed.riskItemCount === 1 ? "risk item" : "risk items"} resolved
        </p>
      ) : (
        <p className="release-review-warning">
          This snapshot was frozen outside Review QA and has no reviewer sign-off.
        </p>
      )}
      <div className="release-export-actions" aria-label={`Version ${number} exports`}>
        <a href={versionExportUrl(version.id, "detection")} download>
          Detection ZIP
          {version.exportTypes.includes("detection") && <small>Previously generated</small>}
        </a>
        <a href={versionExportUrl(version.id, "recognition")} download>
          Recognition ZIP
          {version.exportTypes.includes("recognition") && <small>Previously generated</small>}
        </a>
      </div>
    </article>
  );
}

function VersionFact({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function reviewStatusLabel(
  review: WorkingVersionReview | null,
  available: boolean,
): string {
  if (!available || !review) {
    return "Unavailable";
  }
  return review.unresolvedCount === 0 ? "Ready for sign-off" : `${review.unresolvedCount} unresolved`;
}

function reviewStatusDetail(
  review: WorkingVersionReview | null,
  available: boolean,
): string {
  if (!available || !review) {
    return "Review status could not be loaded. Open Review QA to inspect the current queue.";
  }
  if (review.totalRiskItems === 0) {
    return "No blocking risk items are in the current queue.";
  }
  return `${review.resolvedCount} of ${review.totalRiskItems} risk items resolved.`;
}

function formatDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}

function shortId(value: string): string {
  return value.slice(0, 8);
}

function VersionsMessage({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <main className="project-versions-main">
      <section className="projects-message project-overview-message">
        <img src="/cvsight-mark.svg" alt="" />
        <h1>{title}</h1>
        <p>{detail}</p>
        {action}
      </section>
    </main>
  );
}
