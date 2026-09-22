export const MODEL_ROLES = [
  "known_sku_detector",
  "box_refiner",
  "recognition_embedder",
  "propagation_embedder",
] as const;

export type ModelRole = (typeof MODEL_ROLES)[number];
export type ModelDeploymentStatus = "candidate" | "default" | "previous";

export type ModelEntry = {
  id: string;
  modelRole: string;
  modelId: string;
  modelVersion: string;
  modelArtifactSha256: string;
  evaluationArtifactSha256: string;
  trainingDatasetVersionId: string;
  evaluationDatasetVersionId: string;
  metrics: Record<string, number>;
  registeredBy: string;
  registeredAt: string;
  deploymentStatus: ModelDeploymentStatus;
};

export type ModelDeployment = {
  modelRole: string;
  active: ModelEntry;
  previous: ModelEntry | null;
  updatedBy: string;
  updatedAt: string;
};

export class ModelsApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("model registry request failed");
  }
}

export function isModelRole(value: string): value is ModelRole {
  return (MODEL_ROLES as readonly string[]).includes(value);
}

export async function loadModelCandidates(
  role: ModelRole,
  fetcher: typeof fetch = fetch,
): Promise<ModelEntry[]> {
  const response = await fetcher(`/api/model-registry?model_role=${encodeURIComponent(role)}`, {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  const models = record(body).models;
  if (!Array.isArray(models)) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return models.map(parseEntry);
}

// A role with no promoted model answers 404, which is an ordinary state here.
export async function loadModelDeployment(
  role: ModelRole,
  fetcher: typeof fetch = fetch,
): Promise<ModelDeployment | null> {
  const response = await fetcher(`/api/model-deployments/${encodeURIComponent(role)}`, {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (response.status === 404) {
    return null;
  }
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseDeployment(body);
}

export async function promoteModel(
  role: ModelRole,
  entryId: string,
  expectedActiveId: string | null,
  fetcher: typeof fetch = fetch,
): Promise<ModelDeployment> {
  return deploymentAction(role, "promote", {
    entry_id: uuid(entryId),
    expected_active_id: expectedActiveId === null ? null : uuid(expectedActiveId),
  }, fetcher);
}

export async function rollbackModel(
  role: ModelRole,
  expectedActiveId: string,
  fetcher: typeof fetch = fetch,
): Promise<ModelDeployment> {
  return deploymentAction(role, "rollback", { expected_active_id: uuid(expectedActiveId) }, fetcher);
}

async function deploymentAction(
  role: ModelRole,
  action: "promote" | "rollback",
  payload: Record<string, unknown>,
  fetcher: typeof fetch,
): Promise<ModelDeployment> {
  const response = await fetcher(`/api/model-deployments/${encodeURIComponent(role)}/${action}`, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseDeployment(body);
}

function parseDeployment(value: unknown): ModelDeployment {
  const deployment = record(value);
  return {
    modelRole: requiredString(deployment.model_role),
    active: parseEntry(deployment.active),
    previous: deployment.previous === null || deployment.previous === undefined
      ? null
      : parseEntry(deployment.previous),
    updatedBy: requiredString(deployment.updated_by),
    updatedAt: date(deployment.updated_at),
  };
}

function parseEntry(value: unknown): ModelEntry {
  const entry = record(value);
  const status = entry.deployment_status;
  if (status !== "candidate" && status !== "default" && status !== "previous") {
    throw new ModelsApiError(502, "invalid_response");
  }
  const metrics = record(entry.metrics);
  const numericMetrics: Record<string, number> = {};
  for (const [key, metric] of Object.entries(metrics)) {
    if (typeof metric === "number" && Number.isFinite(metric)) {
      numericMetrics[key] = metric;
    }
  }
  return {
    id: uuid(entry.id),
    modelRole: requiredString(entry.model_role),
    modelId: requiredString(entry.model_id),
    modelVersion: requiredString(entry.model_version),
    modelArtifactSha256: requiredString(entry.model_artifact_sha256),
    evaluationArtifactSha256: requiredString(entry.evaluation_artifact_sha256),
    trainingDatasetVersionId: uuid(entry.training_dataset_version_id),
    evaluationDatasetVersionId: uuid(entry.evaluation_dataset_version_id),
    metrics: numericMetrics,
    registeredBy: requiredString(entry.registered_by),
    registeredAt: date(entry.registered_at),
    deploymentStatus: status,
  };
}

function requestError(status: number, value: unknown): ModelsApiError {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return new ModelsApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object" || Array.isArray(detail)) {
    return new ModelsApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new ModelsApiError(status, typeof code === "string" ? code : "request_failed");
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function requiredString(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return value;
}

function date(value: unknown): string {
  const parsed = requiredString(value);
  if (Number.isNaN(Date.parse(parsed))) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return parsed;
}

function uuid(value: unknown): string {
  const parsed = requiredString(value);
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(parsed)) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return parsed;
}

function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}
