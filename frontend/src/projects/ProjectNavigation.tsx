import type { AuthSession } from "../auth/api";
import type { ProjectSummary } from "./api";

type ProjectNavigationProps = {
  active: "overview" | "images" | "versions" | "models";
  currentUser: AuthSession;
  project: ProjectSummary;
  onImages: () => void;
  onOverview: () => void;
  onReview: () => void;
  onVersions: () => void;
  onModels: () => void;
  onAnalytics: () => void;
};

export function ProjectNavigation({
  active,
  currentUser,
  project,
  onImages,
  onOverview,
  onReview,
  onVersions,
  onModels,
  onAnalytics,
}: ProjectNavigationProps) {
  const canReview = currentUser.role === "owner" || currentUser.role === "reviewer";

  return (
    <nav className="project-navigation" aria-label="Project">
      <button className={active === "overview" ? "is-active" : ""} type="button" onClick={onOverview}>
        Overview
      </button>
      <button className={active === "images" ? "is-active" : ""} type="button" onClick={onImages}>
        Images
      </button>
      <button type="button" disabled title="Choose an image from the Images page">Annotate</button>
      {canReview && (
        <button type="button" disabled={!project.openVersionId} onClick={onReview}>
          Review
        </button>
      )}
      {currentUser.role === "owner" && (
        <>
          <button
            className={active === "versions" ? "is-active" : ""}
            type="button"
            onClick={onVersions}
          >
            Versions
          </button>
          <button
            className={active === "models" ? "is-active" : ""}
            type="button"
            onClick={onModels}
          >
            Models
          </button>
          <button type="button" onClick={onAnalytics}>Analytics</button>
        </>
      )}
    </nav>
  );
}
