import { useEffect, useState } from "react";

import App from "../App";
import type { AuthSession } from "../auth/api";
import type { Workspace } from "../workspace";
import { isMissingProject, loadProject, type ProjectSummary } from "./api";
import { ProjectsHeader } from "./ProjectsHeader";

type ProjectWorkspacePageProps = {
  projectId: string;
  imageId: string | null;
  workspace: Workspace;
  currentUser: AuthSession;
  onBack: () => void;
  onLogout: () => Promise<void>;
  onWorkspaceChange: (workspace: Workspace) => void;
  onOpenImage: (imageId: string) => void;
};

type PageStatus = "loading" | "ready" | "missing" | "error";

export function ProjectWorkspacePage({
  projectId,
  imageId,
  workspace,
  currentUser,
  onBack,
  onLogout,
  onWorkspaceChange,
  onOpenImage,
}: ProjectWorkspacePageProps) {
  const [project, setProject] = useState<ProjectSummary | null>(null);
  const [status, setStatus] = useState<PageStatus>("loading");

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

  if (status !== "ready" || !project) {
    return (
      <div className="projects-shell">
        <ProjectsHeader currentUser={currentUser} section="Project workspace" onLogout={onLogout} />
        <main className="project-overview-main">
          <section className="projects-message">
            <h1>{workspaceMessage(status).title}</h1>
            <p>{workspaceMessage(status).detail}</p>
            {status !== "loading" && (
              <button type="button" onClick={onBack}>Back to project</button>
            )}
          </section>
        </main>
      </div>
    );
  }

  return (
    <App
      key={`${project.id}:${imageId ?? "project"}:${workspace}`}
      currentUser={currentUser}
      onLogout={onLogout}
      datasetVersionId={project.openVersionId ?? project.latestVersionId}
      imageId={imageId}
      initialWorkspace={workspace}
      projectLabel={project.name}
      onExitWorkspace={onBack}
      onWorkspaceChange={onWorkspaceChange}
      datasetId={project.id}
      onOpenImage={onOpenImage}
    />
  );
}

function workspaceMessage(status: PageStatus): { title: string; detail: string } {
  if (status === "loading") {
    return { title: "Loading workspace", detail: "Reading the project workspace." };
  }
  if (status === "missing") {
    return {
      title: "Project not found",
      detail: "This project does not exist or is no longer available.",
    };
  }
  return {
    title: "Project unavailable",
    detail: "The project service could not be reached.",
  };
}
