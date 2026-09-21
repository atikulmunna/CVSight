import { useEffect, useState } from "react";

import App from "../App";
import type { AuthSession } from "../auth/api";
import { workspaceAllowed, type Workspace } from "../workspace";
import type { ProjectSummary } from "./api";
import type { ProjectImage } from "./imagesApi";
import { ProjectImagesPage } from "./ProjectImagesPage";
import { ProjectOverviewPage } from "./ProjectOverviewPage";
import { ProjectVersionsPage } from "./ProjectVersionsPage";
import { ProjectWorkspacePage } from "./ProjectWorkspacePage";
import { ProjectsPage } from "./ProjectsPage";

type ProjectRouterProps = {
  currentUser: AuthSession;
  onLogout: () => Promise<void>;
};

export function ProjectRouter({ currentUser, onLogout }: ProjectRouterProps) {
  const [locationKey, setLocationKey] = useState(() => window.location.href);

  useEffect(() => {
    const updateLocation = () => setLocationKey(window.location.href);
    window.addEventListener("popstate", updateLocation);
    return () => window.removeEventListener("popstate", updateLocation);
  }, []);

  const location = new URL(locationKey);
  const parameters = location.searchParams;
  if (["image", "version", "fixture", "benchmark"].some((key) => parameters.has(key))) {
    return <App currentUser={currentUser} onLogout={onLogout} />;
  }

  const creating =
    currentUser.role === "owner" &&
    location.pathname === "/projects/new";
  const projectId = projectIdFromPath(location.pathname);
  const imagesProjectId = projectImagesIdFromPath(location.pathname);
  const versionsProjectId = projectVersionsIdFromPath(location.pathname);
  const workspaceRoute = projectWorkspaceFromPath(location.pathname);

  function navigate(path: string) {
    window.history.pushState({}, "", path);
    setLocationKey(window.location.href);
  }

  function openProject(project: ProjectSummary) {
    navigate(`/projects/${encodeURIComponent(project.id)}`);
  }

  function openReview(project: ProjectSummary) {
    if (project.openVersionId) {
      navigate(`/projects/${encodeURIComponent(project.id)}/review`);
    }
  }

  function openVersions(project: ProjectSummary) {
    navigate(`/projects/${encodeURIComponent(project.id)}/versions`);
  }

  function openImage(project: ProjectSummary, image: ProjectImage) {
    navigate(`/projects/${encodeURIComponent(project.id)}/annotate/${encodeURIComponent(image.id)}`);
  }

  function openWorkspace(projectId: string, imageId: string | null, workspace: Workspace) {
    const projectPath = `/projects/${encodeURIComponent(projectId)}`;
    if (workspace === "review" || workspace === "catalog" || workspace === "analytics") {
      navigate(`${projectPath}/${workspace}`);
      return;
    }
    if (!imageId) {
      navigate(`${projectPath}/images`);
      return;
    }
    const workspaceSuffix = workspace === "verify" ? "" : `/${workspace}`;
    navigate(`${projectPath}/annotate/${encodeURIComponent(imageId)}${workspaceSuffix}`);
  }

  if (workspaceRoute && workspaceAllowed(currentUser.role, workspaceRoute.workspace)) {
    return (
      <ProjectWorkspacePage
        projectId={workspaceRoute.projectId}
        imageId={workspaceRoute.imageId}
        workspace={workspaceRoute.workspace}
        currentUser={currentUser}
        onBack={() => navigate(
          workspaceRoute.imageId
            ? `/projects/${encodeURIComponent(workspaceRoute.projectId)}/images`
            : `/projects/${encodeURIComponent(workspaceRoute.projectId)}`,
        )}
        onLogout={onLogout}
        onWorkspaceChange={(workspace) => openWorkspace(
          workspaceRoute.projectId,
          workspaceRoute.imageId,
          workspace,
        )}
      />
    );
  }

  if (workspaceRoute) {
    return (
      <ProjectOverviewPage
        key={workspaceRoute.projectId}
        projectId={workspaceRoute.projectId}
        currentUser={currentUser}
        onBack={() => navigate("/projects")}
        onOpenImages={(project) => navigate(`/projects/${encodeURIComponent(project.id)}/images`)}
        onOpenReview={openReview}
        onOpenVersions={openVersions}
        onLogout={onLogout}
      />
    );
  }

  if (imagesProjectId) {
    return (
      <ProjectImagesPage
        projectId={imagesProjectId}
        currentUser={currentUser}
        onBack={() => navigate("/projects")}
        onOpenImage={openImage}
        onOpenOverview={() => navigate(`/projects/${encodeURIComponent(imagesProjectId)}`)}
        onOpenReview={openReview}
        onOpenVersions={openVersions}
        onLogout={onLogout}
      />
    );
  }

  if (versionsProjectId && currentUser.role === "owner") {
    return (
      <ProjectVersionsPage
        key={versionsProjectId}
        projectId={versionsProjectId}
        currentUser={currentUser}
        onBack={() => navigate("/projects")}
        onOpenOverview={() => navigate(`/projects/${encodeURIComponent(versionsProjectId)}`)}
        onOpenImages={() => navigate(`/projects/${encodeURIComponent(versionsProjectId)}/images`)}
        onOpenReview={openReview}
        onLogout={onLogout}
      />
    );
  }

  if (versionsProjectId) {
    return (
      <ProjectOverviewPage
        key={versionsProjectId}
        projectId={versionsProjectId}
        currentUser={currentUser}
        onBack={() => navigate("/projects")}
        onOpenImages={(project) => navigate(`/projects/${encodeURIComponent(project.id)}/images`)}
        onOpenReview={openReview}
        onOpenVersions={openVersions}
        onLogout={onLogout}
      />
    );
  }

  if (projectId) {
    return (
      <ProjectOverviewPage
        key={projectId}
        projectId={projectId}
        currentUser={currentUser}
        onBack={() => navigate("/projects")}
        onOpenImages={(project) => navigate(`/projects/${encodeURIComponent(project.id)}/images`)}
        onOpenReview={openReview}
        onOpenVersions={openVersions}
        onLogout={onLogout}
      />
    );
  }

  return (
    <ProjectsPage
      currentUser={currentUser}
      creating={creating}
      onCancelCreate={() => navigate("/projects")}
      onCreated={(createdProjectId) => navigate(
        `/projects/${encodeURIComponent(createdProjectId)}`,
      )}
      onNewProject={() => navigate("/projects/new")}
      onOpenProject={openProject}
      onLogout={onLogout}
    />
  );
}

type ProjectWorkspaceRoute = {
  projectId: string;
  imageId: string | null;
  workspace: Workspace;
};

function projectWorkspaceFromPath(pathname: string): ProjectWorkspaceRoute | null {
  const annotationMatch = /^\/projects\/([^/]+)\/annotate\/([^/]+)(?:\/(assign|propagate))?$/.exec(pathname);
  if (annotationMatch) {
    const projectId = validProjectId(annotationMatch[1]!);
    const imageId = validProjectId(annotationMatch[2]!);
    if (!projectId || !imageId) {
      return null;
    }
    return {
      projectId,
      imageId,
      workspace: (annotationMatch[3] as Workspace | undefined) ?? "verify",
    };
  }

  const projectMatch = /^\/projects\/([^/]+)\/(review|catalog|analytics)$/.exec(pathname);
  if (!projectMatch) {
    return null;
  }
  const projectId = validProjectId(projectMatch[1]!);
  return projectId
    ? { projectId, imageId: null, workspace: projectMatch[2] as Workspace }
    : null;
}

function projectImagesIdFromPath(pathname: string): string | null {
  const match = /^\/projects\/([^/]+)\/images$/.exec(pathname);
  return match ? validProjectId(match[1]!) : null;
}

function projectVersionsIdFromPath(pathname: string): string | null {
  const match = /^\/projects\/([^/]+)\/versions$/.exec(pathname);
  return match ? validProjectId(match[1]!) : null;
}

function projectIdFromPath(pathname: string): string | null {
  const match = /^\/projects\/([^/]+)$/.exec(pathname);
  if (!match) {
    return null;
  }
  return validProjectId(match[1]!);
}

function validProjectId(value: string): string | null {
  let projectId: string;
  try {
    projectId = decodeURIComponent(value);
  } catch {
    return null;
  }
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(projectId)
    ? projectId
    : null;
}
