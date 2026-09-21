import type { CatalogImportSku } from "./catalogCsv";

export type ProjectSummary = {
  id: string;
  name: string;
  description: string | null;
  createdAt: string;
  openVersionId: string | null;
  latestVersionId: string;
  imageCount: number;
};

export type CreatedProject = {
  id: string;
  openVersionId: string;
  importedSkus: number;
};

export class ProjectApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("project request failed");
  }
}

export async function loadProjects(
  fetcher: typeof fetch = fetch,
): Promise<ProjectSummary[]> {
  const response = await fetcher("/api/datasets", {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  if (!Array.isArray(body)) {
    throw new ProjectApiError(502, "invalid_response");
  }
  return body.map(parseProject);
}

export async function createProject(
  name: string,
  description: string,
  catalog: CatalogImportSku[] = [],
  fetcher: typeof fetch = fetch,
): Promise<CreatedProject> {
  const response = await fetcher("/api/datasets", {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      name: name.trim(),
      description: description.trim() || null,
      catalog,
    }),
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  const project = record(body);
  return {
    id: uuid(project.id),
    openVersionId: uuid(project.open_version_id),
    importedSkus: nonNegativeInteger(project.imported_skus),
  };
}

function parseProject(value: unknown): ProjectSummary {
  const project = record(value);
  const createdAt = requiredString(project.created_at);
  if (Number.isNaN(Date.parse(createdAt))) {
    throw new ProjectApiError(502, "invalid_response");
  }
  return {
    id: uuid(project.id),
    name: requiredString(project.name),
    description: nullableString(project.description),
    createdAt,
    openVersionId:
      project.open_version_id === null ? null : uuid(project.open_version_id),
    latestVersionId: uuid(project.latest_version_id),
    imageCount: nonNegativeInteger(project.image_count),
  };
}

function requestError(status: number, value: unknown): ProjectApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new ProjectApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new ProjectApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new ProjectApiError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProjectApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ProjectApiError(502, "invalid_response");
  }
  return value;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : requiredString(value);
}

function nonNegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ProjectApiError(502, "invalid_response");
  }
  return value;
}

function uuid(value: unknown): string {
  const parsed = requiredString(value);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(parsed)) {
    throw new ProjectApiError(502, "invalid_response");
  }
  return parsed;
}

function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}
