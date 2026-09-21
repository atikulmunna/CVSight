import type { AuthSession } from "../auth/api";
import type { ProjectSummary } from "./api";

type ProjectNavigationProps = {
  active: "overview" | "images" | "versions";
  currentUser: AuthSession;
  project: ProjectSummary;
  onImages: () => void;
  onOverview: () => void;
  onReview: () => void;
  onVersions: () => void;
};

export function ProjectNavigation({
  active,
  currentUser,
  project,
  onImages,
  onOverview,
  onReview,
  onVersions,
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
          <button type="button" disabled>Models</button>
          <button type="button" disabled>Analytics</button>
          <button type="button" disabled>Settings</button>
        </>
      )}
    </nav>
  );
}
