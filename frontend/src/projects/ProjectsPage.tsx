import {
  useEffect,
  useMemo,
  useState,
  type ChangeEvent,
  type FormEvent,
  type ReactNode,
} from "react";

import type { AuthSession } from "../auth/api";
import {
  createProject,
  loadProjects,
  ProjectApiError,
  type ProjectSummary,
} from "./api";
import { CatalogCsvError, parseCatalogCsv, type CatalogImportSku } from "./catalogCsv";
import { ProjectsHeader } from "./ProjectsHeader";

type ProjectsPageProps = {
  currentUser: AuthSession;
  creating: boolean;
  onCancelCreate: () => void;
  onCreated: (projectId: string) => void;
  onNewProject: () => void;
  onOpenProject: (project: ProjectSummary) => void;
  onLogout: () => Promise<void>;
};

export function ProjectsPage({
  currentUser,
  creating,
  onCancelCreate,
  onCreated,
  onNewProject,
  onOpenProject,
  onLogout,
}: ProjectsPageProps) {
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState(false);
  const [search, setSearch] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);

  useEffect(() => {
    let active = true;
    loadProjects()
      .then((loaded) => {
        if (active) {
          setProjects(loaded);
          setLoadError(false);
        }
      })
      .catch(() => {
        if (active) {
          setLoadError(true);
        }
      })
      .finally(() => {
        if (active) {
          setLoading(false);
        }
      });
    return () => {
      active = false;
    };
  }, [refreshKey]);

  const visibleProjects = useMemo(() => {
    const query = search.trim().toLocaleLowerCase();
    if (!query) {
      return projects;
    }
    return projects.filter(
      (project) =>
        project.name.toLocaleLowerCase().includes(query) ||
        project.description?.toLocaleLowerCase().includes(query),
    );
  }, [projects, search]);

  function refreshProjects() {
    setLoading(true);
    setRefreshKey((value) => value + 1);
  }

  return (
    <div className="projects-shell">
      <ProjectsHeader currentUser={currentUser} section="Projects" onLogout={onLogout} />

      <main className="projects-main">
        {creating ? (
          <CreateProjectForm
            onCancel={onCancelCreate}
            onCreated={onCreated}
          />
        ) : (
          <>
            <section className="projects-heading">
              <div>
                <span className="projects-eyebrow">Retail vision workspace</span>
                <h1>Your projects</h1>
                <p>Create a project for each shelf-audit dataset and annotation run.</p>
              </div>
              {currentUser.role === "owner" && (
                <button className="projects-primary-action" type="button" onClick={onNewProject}>
                  <span aria-hidden="true">+</span> New project
                </button>
              )}
            </section>

            <section className="projects-toolbar" aria-label="Project filters">
              <label>
                <span className="sr-only">Search projects</span>
                <input
                  type="search"
                  placeholder="Search projects"
                  value={search}
                  onChange={(event) => setSearch(event.target.value)}
                />
              </label>
              <span>{projects.length} {projects.length === 1 ? "project" : "projects"}</span>
            </section>

            {loading ? (
              <ProjectMessage title="Loading projects" detail="Reading your CVSight workspace." />
            ) : loadError ? (
              <ProjectMessage
                title="Projects unavailable"
                detail="The project service could not be reached."
                action={<button type="button" onClick={refreshProjects}>Try again</button>}
              />
            ) : projects.length === 0 ? (
              <ProjectMessage
                title="Create your first project"
                detail="Projects keep shelf images, annotation versions, and review work together."
                action={currentUser.role === "owner" ? <button type="button" onClick={onNewProject}>New project</button> : undefined}
              />
            ) : visibleProjects.length === 0 ? (
              <ProjectMessage title="No matching projects" detail="Try a different project name or description." />
            ) : (
              <section className="project-grid" aria-label="Projects">
                {visibleProjects.map((project) => (
                  <ProjectCard key={project.id} project={project} onOpen={onOpenProject} />
                ))}
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}

function CreateProjectForm({
  onCancel,
  onCreated,
}: {
  onCancel: () => void;
  onCreated: (projectId: string) => void;
}) {
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [catalog, setCatalog] = useState<CatalogImportSku[]>([]);
  const [catalogFileName, setCatalogFileName] = useState<string | null>(null);
  const [catalogError, setCatalogError] = useState<string | null>(null);
  const [readingCatalog, setReadingCatalog] = useState(false);
  const [catalogInputKey, setCatalogInputKey] = useState(0);

  async function selectCatalog(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0];
    if (!file) {
      return;
    }
    setCatalog([]);
    setCatalogFileName(file.name);
    setCatalogError(null);
    if (file.size > 2 * 1024 * 1024) {
      setCatalogError("The catalog CSV must be 2 MiB or smaller.");
      return;
    }
    try {
      setReadingCatalog(true);
      setCatalog(parseCatalogCsv(await file.text()));
    } catch (caught) {
      setCatalogError(
        caught instanceof CatalogCsvError
          ? caught.message
          : "The catalog CSV could not be read.",
      );
    } finally {
      setReadingCatalog(false);
    }
  }

  function clearCatalog() {
    setCatalog([]);
    setCatalogFileName(null);
    setCatalogError(null);
    setCatalogInputKey((value) => value + 1);
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!name.trim()) {
      setError("Enter a project name.");
      return;
    }
    if (catalogError || readingCatalog) {
      setError("Resolve the catalog CSV before creating the project.");
      return;
    }
    try {
      setSaving(true);
      setError(null);
      const project = await createProject(name, description, catalog);
      onCreated(project.id);
    } catch (caught) {
      setError(projectCreateError(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <section className="create-project-layout">
      <button className="projects-back" type="button" onClick={onCancel}>← Projects</button>
      <div className="create-project-card">
        <span className="projects-eyebrow">New retail vision project</span>
        <h1>Create a project</h1>
        <p>Start with a name. CVSight will create the first editable annotation version automatically.</p>
        <form onSubmit={(event) => void submit(event)}>
          <label>
            Project name
            <input
              autoFocus
              maxLength={255}
              placeholder="Example: Dhaka retail Q3 audit"
              value={name}
              onChange={(event) => setName(event.target.value)}
            />
          </label>
          <label>
            Description <span>Optional</span>
            <textarea
              maxLength={4000}
              placeholder="Stores, audit period, or labeling goal"
              value={description}
              onChange={(event) => setDescription(event.target.value)}
            />
          </label>
          <div className="catalog-import-field">
            <div>
              <strong>SKU catalog CSV <span>Optional</span></strong>
              <p>
                Headers: name, upc, category, subcategory, brand, variant. Name is required.
                Imported SKUs join the catalog shared by every project, so a UPC that already
                exists is rejected.
              </p>
            </div>
            <label>
              <input
                key={catalogInputKey}
                type="file"
                aria-label="SKU catalog CSV"
                accept=".csv,text/csv"
                onChange={(event) => void selectCatalog(event)}
              />
              <span>{catalogFileName ?? "Choose CSV file"}</span>
            </label>
            {readingCatalog && <small>Validating catalog...</small>}
            {!readingCatalog && catalog.length > 0 && (
              <div className="catalog-import-ready">
                <span>{catalog.length} {catalog.length === 1 ? "SKU" : "SKUs"} ready to import</span>
                <button type="button" onClick={clearCatalog}>Remove</button>
              </div>
            )}
            {catalogError && <div className="create-project-error" role="alert">{catalogError}</div>}
          </div>
          {error && <div className="create-project-error" role="alert">{error}</div>}
          <div className="create-project-actions">
            <button type="button" onClick={onCancel}>Cancel</button>
            <button className="projects-primary-action" type="submit" disabled={saving || readingCatalog}>
              {saving ? "Creating project..." : "Create project"}
            </button>
          </div>
        </form>
      </div>
    </section>
  );
}

function ProjectCard({
  project,
  onOpen,
}: {
  project: ProjectSummary;
  onOpen: (project: ProjectSummary) => void;
}) {
  return (
    <article className="project-card">
      <div className="project-card-preview">
        <img src="/cvsight-mark.png" alt="" />
        <span>{project.imageCount === 0 ? "Ready for images" : `${project.imageCount} shelf images`}</span>
      </div>
      <div className="project-card-body">
        <div>
          <h2>{project.name}</h2>
          <p>{project.description ?? "No description added."}</p>
        </div>
        <dl>
          <div><dt>Images</dt><dd>{project.imageCount}</dd></div>
          <div><dt>Created</dt><dd>{formatProjectDate(project.createdAt)}</dd></div>
        </dl>
        <button
          type="button"
          disabled={!project.openVersionId}
          onClick={() => onOpen(project)}
        >
          {project.openVersionId ? "Open project" : "No open version"}
        </button>
      </div>
    </article>
  );
}

function ProjectMessage({
  title,
  detail,
  action,
}: {
  title: string;
  detail: string;
  action?: ReactNode;
}) {
  return (
    <section className="projects-message">
      <img src="/cvsight-mark.png" alt="" />
      <h2>{title}</h2>
      <p>{detail}</p>
      {action}
    </section>
  );
}

function projectCreateError(error: unknown): string {
  if (!(error instanceof ProjectApiError)) {
    return "The project could not be created. Try again.";
  }
  if (error.code === "dataset_name_conflict") {
    return "A project with this name already exists.";
  }
  if (error.code === "duplicate_upc") {
    const upcs = Array.isArray(error.details.upcs)
      ? error.details.upcs.filter((value): value is string => typeof value === "string")
      : [];
    const listed = upcs.length > 0 ? ` (${upcs.join(", ")})` : "";
    return `The catalog is shared by every project, and some UPC values already exist${listed}. Remove or change those rows.`;
  }
  if (error.code === "catalog_constraint_violation") {
    return "The catalog CSV breaks a catalog rule. Check the rows and try again.";
  }
  return "The project could not be created. Try again.";
}

function formatProjectDate(value: string): string {
  return new Intl.DateTimeFormat(undefined, {
    day: "numeric",
    month: "short",
    year: "numeric",
  }).format(new Date(value));
}
