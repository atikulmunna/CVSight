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
  lineage: "snapshot" | "external";
  source: string | null;
  trainingDatasetVersionId: string | null;
  evaluationDatasetVersionId: string;
  // null when the evaluation had no images or boxes for a subset recall.
  metrics: Record<string, number | null>;
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

export type EvaluationState = "queued" | "running" | "succeeded" | "failed" | "cancelled";

export type EvaluationReport = {
  modelId: string;
  modelVersion: string;
  modelArtifactSha256: string;
  photos: number;
  groundTruthBoxes: number;
  predictedBoxes: number;
  confidenceThreshold: number;
  metrics: Record<string, number | null>;
};

export type ModelEvaluation = {
  id: string;
  state: EvaluationState;
  progressCurrent: number;
  progressTotal: number | null;
  errorCode: string | null;
  report: EvaluationReport | null;
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

export async function loadEvaluationDefaults(fetcher: typeof fetch = fetch): Promise<string | null> {
  const response = await fetcher("/api/model-registry/evaluations/defaults", {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  const runtimeUrl = record(body).runtime_url;
  return typeof runtimeUrl === "string" && runtimeUrl ? runtimeUrl : null;
}

export async function startModelEvaluation(
  request: { datasetVersionId: string; runtimeUrl: string; confidenceThreshold: number },
  fetcher: typeof fetch = fetch,
): Promise<ModelEvaluation> {
  const response = await fetcher("/api/model-registry/evaluations", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      dataset_version_id: request.datasetVersionId,
      runtime_url: request.runtimeUrl,
      confidence_threshold: request.confidenceThreshold,
    }),
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseEvaluation(body);
}

export async function loadModelEvaluation(
  evaluationId: string,
  fetcher: typeof fetch = fetch,
): Promise<ModelEvaluation> {
  const response = await fetcher(`/api/jobs/${encodeURIComponent(uuid(evaluationId))}`, {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseEvaluation(body);
}

export async function registerEvaluatedModel(
  evaluationId: string,
  source: string,
  licensesApproved: boolean,
  fetcher: typeof fetch = fetch,
): Promise<ModelEntry> {
  const response = await fetcher(
    `/api/model-registry/evaluations/${encodeURIComponent(uuid(evaluationId))}/register`,
    {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({ source, licenses_approved: licensesApproved }),
    },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw requestError(response.status, body);
  }
  return parseEntry(body);
}

function parseEvaluation(value: unknown): ModelEvaluation {
  const job = record(value);
  const state = job.state;
  if (
    job.job_type !== "evaluate_detector" ||
    (state !== "queued" && state !== "running" && state !== "succeeded" &&
      state !== "failed" && state !== "cancelled")
  ) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return {
    id: uuid(job.id),
    state,
    progressCurrent: count(job.progress_current),
    progressTotal: job.progress_total === null ? null : count(job.progress_total),
    errorCode: typeof job.error_code === "string" ? job.error_code : null,
    report: state === "succeeded" ? parseReport(job.result) : null,
  };
}

function parseReport(value: unknown): EvaluationReport {
  const report = record(value);
  const threshold = report.confidence_threshold;
  if (typeof threshold !== "number") {
    throw new ModelsApiError(502, "invalid_response");
  }
  return {
    modelId: requiredString(report.model_id),
    modelVersion: requiredString(report.model_version),
    modelArtifactSha256: requiredString(report.model_artifact_sha256),
    photos: count(report.photos),
    groundTruthBoxes: count(report.ground_truth_boxes),
    predictedBoxes: count(report.predicted_boxes),
    confidenceThreshold: threshold,
    metrics: parseMetrics(report.metrics),
  };
}

// Numeric scores, plus null for a subset recall the evaluation could not measure.
function parseMetrics(value: unknown): Record<string, number | null> {
  const metrics: Record<string, number | null> = {};
  for (const [key, metric] of Object.entries(record(value))) {
    if (metric === null || (typeof metric === "number" && Number.isFinite(metric))) {
      metrics[key] = metric;
    }
  }
  return metrics;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ModelsApiError(502, "invalid_response");
  }
  return value;
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
  const numericMetrics = parseMetrics(entry.metrics);
  return {
    id: uuid(entry.id),
    modelRole: requiredString(entry.model_role),
    modelId: requiredString(entry.model_id),
    modelVersion: requiredString(entry.model_version),
    modelArtifactSha256: requiredString(entry.model_artifact_sha256),
    evaluationArtifactSha256: requiredString(entry.evaluation_artifact_sha256),
    ...lineage(entry),
    evaluationDatasetVersionId: uuid(entry.evaluation_dataset_version_id),
    metrics: numericMetrics,
    registeredBy: requiredString(entry.registered_by),
    registeredAt: date(entry.registered_at),
    deploymentStatus: status,
  };
}

// A snapshot-trained model names its training version; an external one names its source.
function lineage(
  entry: Record<string, unknown>,
): Pick<ModelEntry, "lineage" | "source" | "trainingDatasetVersionId"> {
  if (entry.lineage === "snapshot") {
    return {
      lineage: "snapshot",
      source: null,
      trainingDatasetVersionId: uuid(entry.training_dataset_version_id),
    };
  }
  if (entry.lineage === "external" && entry.training_dataset_version_id === null) {
    return { lineage: "external", source: requiredString(entry.source), trainingDatasetVersionId: null };
  }
  throw new ModelsApiError(502, "invalid_response");
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
