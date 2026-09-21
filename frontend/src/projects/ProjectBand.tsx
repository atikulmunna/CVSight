import type { ReactNode } from "react";

import type { AuthSession } from "../auth/api";
import type { ProjectSummary } from "./api";
import { ProjectNavigation } from "./ProjectNavigation";

type ProjectBandProps = {
  project: ProjectSummary;
  currentUser: AuthSession;
  active: "overview" | "images" | "versions";
  status?: ReactNode;
  rail?: ShelfRailProps | null;
  onBack: () => void;
  onOverview: () => void;
  onImages: () => void;
  onReview: () => void;
  onVersions: () => void;
};

export function ProjectBand({
  project,
  currentUser,
  active,
  status,
  rail,
  onBack,
  onOverview,
  onImages,
  onReview,
  onVersions,
}: ProjectBandProps) {
  return (
    <section className="project-band">
      <div className="project-band-inner">
        <button className="projects-back" type="button" onClick={onBack}>All projects</button>
        <div className="project-band-title">
          <div>
            <h1>{project.name}</h1>
            <p>{project.description ?? "No project description has been added."}</p>
          </div>
          {status}
        </div>
        {rail && <ShelfRail total={rail.total} reviewed={rail.reviewed} />}
        <ProjectNavigation
          active={active}
          currentUser={currentUser}
          project={project}
          onOverview={onOverview}
          onImages={onImages}
          onReview={onReview}
          onVersions={onVersions}
        />
      </div>
    </section>
  );
}

type ShelfRailProps = {
  total: number;
  reviewed: number;
};

const RAIL_SLOTS = 36;

// The rail draws review progress as shelf facings filling up instead of a plain bar.
export function ShelfRail({ total, reviewed }: ShelfRailProps) {
  const filled = total === 0 ? 0 : Math.round((Math.min(reviewed, total) / total) * RAIL_SLOTS);
  const label =
    total === 0
      ? "No shelf images yet"
      : `${reviewed} of ${total} ${total === 1 ? "image" : "images"} reviewed`;
  return (
    <div className="shelf-rail" role="img" aria-label={label}>
      <span className="shelf-rail-slots" aria-hidden="true">
        {Array.from({ length: RAIL_SLOTS }, (_, index) => (
          <span key={index} className={index < filled ? "is-filled" : undefined} />
        ))}
      </span>
      <span className="shelf-rail-label" aria-hidden="true">{label}</span>
    </div>
  );
}
