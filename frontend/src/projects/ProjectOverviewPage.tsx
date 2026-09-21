import { useEffect, useState, type ReactNode } from "react";

import type { AuthSession } from "../auth/api";
import { isMissingProject, loadProject, type ProjectSummary } from "./api";
import { ProjectBand } from "./ProjectBand";
import { ProjectsHeader } from "./ProjectsHeader";
import { loadProjectProgress, type ProjectProgress } from "./progressApi";

type ProjectOverviewPageProps = {
  projectId: string;
  currentUser: AuthSession;
  onBack: () => void;
  onOpenImages: (project: ProjectSummary) => void;
  onOpenReview: (project: ProjectSummary) => void;
  onOpenVersions: (project: ProjectSummary) => void;
  onLogout: () => Promise<void>;
};

export function ProjectOverviewPage({
  projectId,
  currentUser,
  onBack,
  onOpenImages,
  onOpenReview,
  onOpenVersions,
  onLogout,
}: ProjectOverviewPageProps) {
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [status, setStatus] = useState<"loading" | "ready" | "missing" | "error">(
    "loading",
  );
  const [progress, setProgress] = useState<ProjectProgress | null>(null);
  const [progressStatus, setProgressStatus] = useState<"loading" | "ready" | "error">(
    "loading",
  );
  const [progressRefreshKey, setProgressRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    loadProject(projectId)
      .then((selected) => {
        if (!active) {
          return;
        }
        setProject(selected);
        setStatus("ready");
      })
      .catch((error: unknown) => {
        if (!active) {
          return;
        }
        setProject(null);
        setStatus(isMissingProject(error) ? "missing" : "error");
      });
    return () => {
      active = false;
    };
  }, [projectId]);

  useEffect(() => {
    if (!project) {
      return;
    }
    let active = true;
    loadProjectProgress(project.openVersionId ?? project.latestVersionId)
      .then((loaded) => {
        if (active) {
          setProgress(loaded);
          setProgressStatus("ready");
        }
      })
      .catch(() => {
        if (active) {
          setProgress(null);
          setProgressStatus("error");
        }
      });
    return () => {
      active = false;
    };
  }, [project, progressRefreshKey]);

  return (
    <div className="projects-shell">
      <ProjectsHeader currentUser={currentUser} section="Project overview" onLogout={onLogout} />
      {status === "loading" ? (
        <OverviewMessage title="Loading project" detail="Reading the project workspace." />
      ) : status === "error" ? (
        <OverviewMessage
          title="Project unavailable"
          detail="The project service could not be reached."
          action={<button type="button" onClick={onBack}>Back to projects</button>}
        />
      ) : status === "missing" || !project ? (
        <OverviewMessage
          title="Project not found"
          detail="This project does not exist or is no longer available."
          action={<button type="button" onClick={onBack}>Back to projects</button>}
        />
      ) : (
        <ProjectOverview
          project={project}
          currentUser={currentUser}
          onBack={onBack}
          onOpenImages={onOpenImages}
          onOpenReview={onOpenReview}
          onOpenVersions={onOpenVersions}
          progress={progress}
          progressStatus={progressStatus}
          onRetryProgress={() => {
            setProgressStatus("loading");
            setProgressRefreshKey((value) => value + 1);
          }}
        />
      )}
    </div>
  );
}

function ProjectOverview({
  project,
  currentUser,
  onBack,
  onOpenImages,
  onOpenReview,
  onOpenVersions,
  progress,
  progressStatus,
  onRetryProgress,
}: {
  project: ProjectSummary;
  currentUser: AuthSession;
  onBack: () => void;
  onOpenImages: (project: ProjectSummary) => void;
  onOpenReview: (project: ProjectSummary) => void;
  onOpenVersions: (project: ProjectSummary) => void;
  progress: ProjectProgress | null;
  progressStatus: "loading" | "ready" | "error";
  onRetryProgress: () => void;
}) {
  const canReview = currentUser.role === "owner" || currentUser.role === "reviewer";
  const reviewReady = canReview && project.openVersionId !== null;

  return (
    <main className="project-overview-main">
      <ProjectBand
        project={project}
        currentUser={currentUser}
        active="overview"
        status={
          <span className={`project-version-state ${project.openVersionId ? "open" : "frozen"}`}>
            {project.openVersionId ? "Working version" : "No working version"}
          </span>
        }
        rail={
          progressStatus === "ready" && progress
            ? { total: progress.images.total, reviewed: progress.qa.reviewedImages }
            : null
        }
        onBack={onBack}
        onOverview={() => undefined}
        onImages={() => onOpenImages(project)}
        onReview={() => onOpenReview(project)}
        onVersions={() => onOpenVersions(project)}
      />

      {currentUser.role === "owner" &&
        progressStatus === "ready" &&
        progress &&
        progress.qa.reviewedImages === 0 && (
        <FirstImageChecklist
          hasImages={progress.images.total > 0}
          onOpenImages={() => onOpenImages(project)}
        />
      )}

      <section className="project-overview-metrics" aria-label="Project status">
        <OverviewMetric label="Images" value={String(project.imageCount)} detail="Managed shelf photos" />
        <OverviewMetric
          label="Working version"
          value={project.openVersionId ? "Open" : "None"}
          detail={project.openVersionId ? "Ready for annotation work" : "Latest version is frozen"}
        />
        <OverviewMetric label="Created" value={formatProjectDate(project.createdAt)} detail="Project start date" />
      </section>

      <ProjectProgressSection
        progress={progress}
        status={progressStatus}
        onRetry={onRetryProgress}
      />

      <section className="project-next-section">
        <div className="project-next-heading">
          <h2>What happens next</h2>
          <p>Move from shelf images to reviewed, export-ready annotations.</p>
        </div>
        <div className="project-action-grid">
          <ProjectAction
            step="01"
            title="Add shelf images"
            detail={
              project.imageCount === 0
                ? "Upload the first JPEG or PNG shelf photos."
                : `${project.imageCount} images are available in this project.`
            }
            state={project.imageCount === 0 ? "Next" : "Complete"}
            action={<button type="button" onClick={() => onOpenImages(project)}>Open images</button>}
          />
          <ProjectAction
            step="02"
            title="Annotate products"
            detail={annotationActionDetail(project, progress)}
            state={annotationActionState(project, progress, progressStatus)}
            action={
              project.imageCount > 0 ? (
                <button type="button" onClick={() => onOpenImages(project)}>Continue annotation</button>
              ) : undefined
            }
          />
          <ProjectAction
            step="03"
            title="Review and release"
            detail={reviewActionDetail(project, progress)}
            state={reviewActionState(project, progress, progressStatus, reviewReady)}
            action={
              reviewReady ? (
                <button type="button" onClick={() => onOpenReview(project)}>Open Review QA</button>
              ) : undefined
            }
          />
        </div>
      </section>
    </main>
  );
}

function FirstImageChecklist({
  hasImages,
  onOpenImages,
}: {
  hasImages: boolean;
  onOpenImages: () => void;
}) {
  return (
    <section className="first-image-checklist" aria-labelledby="first-image-checklist-title">
      <div className="first-image-checklist-heading">
        <div>
          <h2 id="first-image-checklist-title">First image checklist</h2>
          <p>{hasImages ? "2 of 3 steps complete" : "1 of 3 steps complete"}</p>
        </div>
        <button className="projects-primary-action" type="button" onClick={onOpenImages}>
          {hasImages ? "Open first image" : "Upload first image"}
        </button>
      </div>
      <ol>
        <ChecklistStep step={1} title="Project created" detail="Your editable working version is ready." state="complete" />
        <ChecklistStep
          step={2}
          title="Add a shelf image"
          detail="Upload a JPEG or PNG shelf photo to managed project storage."
          state={hasImages ? "complete" : "current"}
        />
        <ChecklistStep
          step={3}
          title="Review the first image"
          detail="Correct boxes, record every decision, assign identities as needed, wait for All changes saved, then choose Mark reviewed."
          state={hasImages ? "current" : "waiting"}
        />
      </ol>
    </section>
  );
}

function ChecklistStep({
  step,
  title,
  detail,
  state,
}: {
  step: number;
  title: string;
  detail: string;
  state: "complete" | "current" | "waiting";
}) {
  return (
    <li className={state} aria-current={state === "current" ? "step" : undefined}>
      <span aria-hidden="true">{state === "complete" ? "✓" : step}</span>
      <div>
        <strong>{title}</strong>
        <p>{detail}</p>
      </div>
    </li>
  );
}

function ProjectProgressSection({
  progress,
  status,
  onRetry,
}: {
  progress: ProjectProgress | null;
  status: "loading" | "ready" | "error";
  onRetry: () => void;
}) {
  if (status === "loading") {
    return (
      <section className="project-progress-section" aria-label="Project progress">
        <h2>Project progress</h2>
        <p className="project-progress-message">Loading annotation progress...</p>
      </section>
    );
  }
  if (status === "error" || !progress) {
    return (
      <section className="project-progress-section" aria-label="Project progress">
        <h2>Project progress</h2>
        <div className="project-progress-error">
          <div>
            <strong>Progress unavailable</strong>
            <p>The project is still accessible. Retry the progress summary.</p>
          </div>
          <button type="button" onClick={onRetry}>Retry progress</button>
        </div>
      </section>
    );
  }

  return (
    <section className="project-progress-section" aria-label="Project progress">
      <h2>Project progress</h2>
      <div className="project-progress-grid">
        <ProgressCard
          label="Box decisions"
          done={progress.annotations.decided}
          total={progress.annotations.total}
          detail={annotationProgressDetail(progress)}
        />
        <ProgressCard
          label="Known identities"
          done={progress.identity.knownProducts}
          total={progress.identity.acceptedProducts}
          detail={identityProgressDetail(progress)}
        />
        <ProgressCard
          label="QA reviewed"
          done={progress.qa.reviewedImages}
          total={progress.images.total}
          detail={qaProgressDetail(progress)}
        />
      </div>
    </section>
  );
}

function ProgressCard({
  label,
  done,
  total,
  detail,
}: {
  label: string;
  done: number;
  total: number;
  detail: string;
}) {
  const percent = total === 0 ? 0 : Math.round((done / total) * 100);
  return (
    <article className="project-progress-card">
      <div className="project-progress-card-heading">
        <span>{label}</span>
        <strong>{done}/{total}</strong>
      </div>
      <div
        className="project-progress-track"
        role="progressbar"
        aria-label={label}
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={done}
      >
        <span style={{ width: `${percent}%` }} />
      </div>
      <p>{detail}</p>
    </article>
  );
}

function annotationProgressDetail(progress: ProjectProgress): string {
  const remaining = progress.annotations.total - progress.annotations.decided;
  if (progress.annotations.total === 0) {
    return "No boxes have been added yet.";
  }
  return remaining === 0
    ? "Every box has an accept or reject decision."
    : `${remaining} boxes still need accept or reject.`;
}

function identityProgressDetail(progress: ProjectProgress): string {
  if (progress.identity.acceptedProducts === 0) {
    return "Accept product boxes before assigning identities.";
  }
  return `${progress.identity.knownProducts} known · ${progress.identity.unknownProducts} Unknown · ${progress.identity.unassignedProducts} unassigned`;
}

function qaProgressDetail(progress: ProjectProgress): string {
  if (progress.images.total === 0) {
    return "Add images before starting QA.";
  }
  const flagLabel = progress.qa.flaggedAnnotations === 1 ? "annotation" : "annotations";
  return `${progress.qa.reviewedImages} reviewed · ${progress.qa.flaggedAnnotations} flagged ${flagLabel}`;
}

function annotationActionState(
  project: ProjectSummary,
  progress: ProjectProgress | null,
  status: "loading" | "ready" | "error",
): string {
  if (project.imageCount === 0) {
    return "Waiting for images";
  }
  if (status !== "ready" || !progress) {
    return "Open images";
  }
  const decisionsLeft = progress.annotations.total - progress.annotations.decided;
  if (progress.annotations.total === 0) {
    return "Start annotating";
  }
  if (decisionsLeft > 0) {
    return `${decisionsLeft} decisions left`;
  }
  const identitiesLeft = progress.identity.acceptedProducts - progress.identity.knownProducts;
  return identitiesLeft > 0 ? `${identitiesLeft} identities left` : "Complete";
}

function annotationActionDetail(
  project: ProjectSummary,
  progress: ProjectProgress | null,
): string {
  if (project.imageCount === 0 || !progress || progress.annotations.total === 0) {
    return "Verify product boxes, assign SKU identities, and save every revision.";
  }
  const decisionsLeft = progress.annotations.total - progress.annotations.decided;
  if (decisionsLeft > 0) {
    return `Record decisions for ${decisionsLeft} remaining boxes before identity cleanup.`;
  }
  const identitiesLeft = progress.identity.acceptedProducts - progress.identity.knownProducts;
  return identitiesLeft > 0
    ? `Resolve ${identitiesLeft} Unknown or unassigned product identities.`
    : "Box verification and known SKU identity work are complete.";
}

function reviewActionState(
  project: ProjectSummary,
  progress: ProjectProgress | null,
  status: "loading" | "ready" | "error",
  reviewReady: boolean,
): string {
  if (project.imageCount === 0) {
    return "Waiting for images";
  }
  if (status !== "ready" || !progress) {
    return reviewReady ? "Open QA" : "Owner or reviewer access";
  }
  if (progress.qa.flaggedAnnotations > 0) {
    return progress.qa.flaggedAnnotations === 1
      ? "1 flag remains"
      : `${progress.qa.flaggedAnnotations} flags remain`;
  }
  const imagesLeft = progress.images.total - progress.qa.reviewedImages;
  if (imagesLeft > 0) {
    return `${imagesLeft} images left`;
  }
  return reviewReady ? "Ready for QA" : "Complete";
}

function reviewActionDetail(
  project: ProjectSummary,
  progress: ProjectProgress | null,
): string {
  if (project.imageCount === 0 || !progress) {
    return "Resolve QA risks before freezing an immutable dataset version.";
  }
  const imagesLeft = progress.images.total - progress.qa.reviewedImages;
  if (progress.qa.flaggedAnnotations > 0) {
    const label = progress.qa.flaggedAnnotations === 1 ? "annotation" : "annotations";
    return `Resolve ${progress.qa.flaggedAnnotations} flagged ${label} before release.`;
  }
  return imagesLeft > 0
    ? `Complete image review for ${imagesLeft} remaining images.`
    : "Image review is complete. Resolve the risk queue before release.";
}

function OverviewMetric({
  label,
  value,
  detail,
}: {
  label: string;
  value: string;
  detail: string;
}) {
  return (
    <article>
      <span>{label}</span>
      <strong>{value}</strong>
      <p>{detail}</p>
    </article>
  );
}

function ProjectAction({
  step,
  title,
  detail,
  state,
  action,
}: {
  step: string;
  title: string;
  detail: string;
  state: string;
  action?: ReactNode;
}) {
  return (
    <article>
      <div className="project-action-number">{step}</div>
      <span className="project-action-state">{state}</span>
      <h3>{title}</h3>
      <p>{detail}</p>
      {action}
    </article>
  );
}

function OverviewMessage({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <main className="project-overview-main">
      <section className="projects-message project-overview-message">
        <img src="/cvsight-mark.svg" alt="" />
        <h1>{title}</h1>
        <p>{detail}</p>
        {action}
      </section>
    </main>
  );
}

function formatProjectDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}
