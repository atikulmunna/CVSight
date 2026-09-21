export type ProjectProgress = {
  datasetVersionId: string;
  images: {
    total: number;
  };
  annotations: {
    total: number;
    decided: number;
  };
  identity: {
    acceptedProducts: number;
    knownProducts: number;
    unknownProducts: number;
    unassignedProducts: number;
  };
  qa: {
    reviewedImages: number;
    flaggedAnnotations: number;
  };
};

export class ProgressApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("project progress request failed");
  }
}

export async function loadProjectProgress(
  datasetVersionId: string,
  fetcher: typeof fetch = fetch,
): Promise<ProjectProgress> {
  const versionId = uuid(datasetVersionId);
  const response = await fetcher(
    `/api/dataset-versions/${encodeURIComponent(versionId)}/progress`,
    { headers: { Accept: "application/json" } },
  );
  const body = await response.json().catch(() => null) as unknown;
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  const progress = parseProgress(body);
  if (progress.datasetVersionId !== versionId) {
    throw new ProgressApiError(502, "invalid_response");
  }
  return progress;
}

function parseProgress(value: unknown): ProjectProgress {
  const data = record(value);
  const images = record(data.images);
  const annotations = record(data.annotations);
  const identity = record(data.identity);
  const qa = record(data.qa);
  const progress: ProjectProgress = {
    datasetVersionId: uuid(data.dataset_version_id),
    images: {
      total: nonNegativeInteger(images.total),
    },
    annotations: {
      total: nonNegativeInteger(annotations.total),
      decided: nonNegativeInteger(annotations.decided),
    },
    identity: {
      acceptedProducts: nonNegativeInteger(identity.accepted_products),
      knownProducts: nonNegativeInteger(identity.known_products),
      unknownProducts: nonNegativeInteger(identity.unknown_products),
      unassignedProducts: nonNegativeInteger(identity.unassigned_products),
    },
    qa: {
      reviewedImages: nonNegativeInteger(qa.reviewed_images),
      flaggedAnnotations: nonNegativeInteger(qa.flagged_annotations),
    },
  };
  if (
    progress.qa.reviewedImages > progress.images.total ||
    progress.annotations.decided > progress.annotations.total ||
    progress.identity.acceptedProducts > progress.annotations.decided ||
    progress.identity.knownProducts +
      progress.identity.unknownProducts +
      progress.identity.unassignedProducts !==
      progress.identity.acceptedProducts ||
    progress.qa.flaggedAnnotations > progress.annotations.total
  ) {
    throw new ProgressApiError(502, "invalid_response");
  }
  return progress;
}

function requestError(status: number, value: unknown): ProgressApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new ProgressApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new ProgressApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new ProgressApiError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProgressApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function nonNegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ProgressApiError(502, "invalid_response");
  }
  return value;
}

function uuid(value: unknown): string {
  if (
    typeof value !== "string" ||
    !/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
  ) {
    throw new ProgressApiError(502, "invalid_response");
  }
  return value;
}
