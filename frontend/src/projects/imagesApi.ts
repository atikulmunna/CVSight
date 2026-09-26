export type ProjectImageStatus =
  | "unlabeled"
  | "pre_labeled"
  | "in_progress"
  | "labeled"
  | "reviewed";

export type ProjectImage = {
  id: string;
  originalFilename: string;
  mediaType: "image/jpeg" | "image/png";
  width: number;
  height: number;
  status: ProjectImageStatus;
  createdAt: string;
  thumbnailUrl: string;
};

export type ProjectImagePage = {
  images: ProjectImage[];
  total: number;
  limit: number;
  offset: number;
};

export type ImageNeighbors = {
  previousId: string | null;
  nextId: string | null;
  position: number;
  total: number;
};

export class ProjectImageApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("project image request failed");
  }
}

export async function loadProjectImages(
  projectId: string,
  versionId: string,
  status: ProjectImageStatus | null,
  offset = 0,
  limit = 60,
  fetcher: typeof fetch = fetch,
): Promise<ProjectImagePage> {
  requireUuid(projectId);
  requireUuid(versionId);
  const parameters = new URLSearchParams({
    limit: String(limit),
    offset: String(offset),
  });
  if (status) {
    parameters.set("status", status);
  }
  const response = await fetcher(
    `/api/datasets/${encodeURIComponent(projectId)}` +
      `/versions/${encodeURIComponent(versionId)}/images?${parameters}`,
    { headers: { Accept: "application/json" } },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parsePage(body);
}

export async function loadImageNeighbors(
  projectId: string,
  versionId: string,
  imageId: string,
  fetcher: typeof fetch = fetch,
): Promise<ImageNeighbors> {
  requireUuid(projectId);
  requireUuid(versionId);
  requireUuid(imageId);
  const response = await fetcher(
    `/api/datasets/${encodeURIComponent(projectId)}` +
      `/versions/${encodeURIComponent(versionId)}` +
      `/images/${encodeURIComponent(imageId)}/neighbors`,
    { headers: { Accept: "application/json" } },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  const value = record(body);
  return {
    previousId: value.previous_id === null ? null : uuid(value.previous_id),
    nextId: value.next_id === null ? null : uuid(value.next_id),
    position: positiveInteger(value.position),
    total: positiveInteger(value.total),
  };
}

export async function uploadProjectImage(
  projectId: string,
  versionId: string,
  file: File,
  fetcher: typeof fetch = fetch,
): Promise<void> {
  requireUuid(projectId);
  requireUuid(versionId);
  const form = new FormData();
  form.append("file", file);
  const response = await fetcher(
    `/api/datasets/${encodeURIComponent(projectId)}` +
      `/versions/${encodeURIComponent(versionId)}/images`,
    {
      method: "POST",
      headers: { Accept: "application/json" },
      body: form,
    },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
}

function parsePage(value: unknown): ProjectImagePage {
  const page = record(value);
  if (!Array.isArray(page.images)) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return {
    images: page.images.map(parseImage),
    total: nonNegativeInteger(page.total),
    limit: positiveInteger(page.limit),
    offset: nonNegativeInteger(page.offset),
  };
}

function parseImage(value: unknown): ProjectImage {
  const image = record(value);
  const mediaType = requiredString(image.media_type);
  const createdAt = requiredString(image.created_at);
  if (
    (mediaType !== "image/jpeg" && mediaType !== "image/png") ||
    Number.isNaN(Date.parse(createdAt))
  ) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return {
    id: uuid(image.id),
    originalFilename: requiredString(image.original_filename),
    mediaType,
    width: positiveInteger(image.width),
    height: positiveInteger(image.height),
    status: imageStatus(image.status),
    createdAt,
    thumbnailUrl: safeThumbnailUrl(image.thumbnail_url),
  };
}

function requestError(status: number, value: unknown): ProjectImageApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new ProjectImageApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new ProjectImageApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new ProjectImageApiError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

function imageStatus(value: unknown): ProjectImageStatus {
  if (
    value !== "unlabeled" &&
    value !== "pre_labeled" &&
    value !== "in_progress" &&
    value !== "labeled" &&
    value !== "reviewed"
  ) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value;
}

function safeThumbnailUrl(value: unknown): string {
  const url = requiredString(value);
  if (!url.startsWith("/api/images/") || url.includes("\\") || url.includes("..")) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return url;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value;
}

function nonNegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value;
}

function positiveInteger(value: unknown): number {
  const parsed = nonNegativeInteger(value);
  if (parsed < 1) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return parsed;
}

function uuid(value: unknown): string {
  const parsed = requiredString(value);
  requireUuid(parsed);
  return parsed;
}

function requireUuid(value: string): void {
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)) {
    throw new ProjectImageApiError(400, "invalid_id");
  }
}

function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}
