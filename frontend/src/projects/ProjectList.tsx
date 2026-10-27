import { PROJECT_TYPE_LABELS, type ProjectSummary } from "./api";
import type { ProjectProgress } from "./progressApi";

// Absent while loading; "unavailable" when the request failed.
export type ReviewProgress = ProjectProgress | "unavailable";

type ProjectListProps = {
  projects: ProjectSummary[];
  totalProjects: number;
  previews: Record<string, string[]>;
  progress: Record<string, ReviewProgress>;
  onOpen: (project: ProjectSummary) => void;
};

export function ProjectList({
  projects,
  totalProjects,
  previews,
  progress,
  onOpen,
}: ProjectListProps) {
  return (
    <table className="project-list">
      <caption>{totalProjects} {totalProjects === 1 ? "project" : "projects"}</caption>
      <thead>
        <tr>
          <th scope="col"><span className="sr-only">Photos</span></th>
          <th scope="col">Project</th>
          <th scope="col">Type</th>
          <th scope="col">Images</th>
          <th scope="col">Reviewed</th>
          <th scope="col">Working version</th>
          <th scope="col">Created</th>
          <th scope="col"><span className="sr-only">Actions</span></th>
        </tr>
      </thead>
      <tbody>
        {projects.map((project) => (
          <ProjectRow
            key={project.id}
            project={project}
            previews={previews[project.id] ?? []}
            progress={progress[project.id]}
            onOpen={onOpen}
          />
        ))}
      </tbody>
    </table>
  );
}

function ProjectRow({
  project,
  previews,
  progress,
  onOpen,
}: {
  project: ProjectSummary;
  previews: string[];
  progress: ReviewProgress | undefined;
  onOpen: (project: ProjectSummary) => void;
}) {
  return (
    <tr>
      <td className="project-list-photos">
        <div className={previews.length > 0 ? "has-photos" : undefined}>
          {previews.length > 0 ? (
            previews.map((url) => <img key={url} src={url} alt="" loading="lazy" />)
          ) : (
            <img src="/cvsight-mark.svg" alt="" />
          )}
        </div>
      </td>
      <th scope="row" className="project-list-name">
        <strong>{project.name}</strong>
        {project.description && <span>{project.description}</span>}
      </th>
      <td data-label="Type">{PROJECT_TYPE_LABELS[project.projectType]}</td>
      <td data-label="Images" className="project-list-number">{project.imageCount}</td>
      <td data-label="Reviewed"><ReviewedProgress progress={progress} /></td>
      <td data-label="Working version">{project.openVersionId ? "Open" : "None"}</td>
      <td data-label="Created" className="project-list-number">{formatProjectDate(project.createdAt)}</td>
      <td className="project-list-action">
        <button
          type="button"
          aria-label={`Open ${project.name}`}
          disabled={!project.openVersionId}
          onClick={() => onOpen(project)}
        >
          Open
        </button>
      </td>
    </tr>
  );
}

function ReviewedProgress({ progress }: { progress: ReviewProgress | undefined }) {
  if (progress === undefined) {
    return <span className="project-list-muted">Loading...</span>;
  }
  if (progress === "unavailable") {
    return <span className="project-list-muted">Not available</span>;
  }
  const total = progress.images.total;
  if (total === 0) {
    return <span className="project-list-muted">No images yet</span>;
  }
  const reviewed = progress.qa.reviewedImages;
  return (
    <div className="project-list-progress">
      <span className="project-list-progress-track" aria-hidden="true">
        <span style={{ width: `${Math.round((reviewed / total) * 100)}%` }} />
      </span>
      <span>{reviewed} of {total} reviewed</span>
    </div>
  );
}

function formatProjectDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}
