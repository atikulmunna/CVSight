// Test fixtures shared by specs that need a promoted detector; not used by the app.

export const PROMOTED_DETECTOR_ID = "99999999-9999-4999-8999-999999999999";

export function promotedDetectorDeployment() {
  const artifactId = "88888888-8888-4888-8888-888888888888";
  return {
    model_role: "known_sku_detector",
    active: {
      id: PROMOTED_DETECTOR_ID,
      model_role: "known_sku_detector",
      model_id: "shelf-detector",
      model_version: "v1",
      model_artifact_id: artifactId,
      evaluation_artifact_id: artifactId,
      model_artifact_key: "models/shelf-detector.json",
      evaluation_artifact_key: "evaluations/shelf-detector.json",
      lineage: "external",
      source: "Two-stage shelf detector",
      training_dataset_version_id: null,
      evaluation_dataset_version_id: artifactId,
      model_artifact_sha256: "a".repeat(64),
      evaluation_artifact_sha256: "b".repeat(64),
      configuration: {},
      compatibility: {},
      metrics: { map_50_95: 0.8 },
      registered_by: "owner:owner",
      registered_at: "2026-09-26T08:00:00Z",
      deployment_status: "default",
    },
    previous: null,
    updated_by: "owner:owner",
    updated_at: "2026-09-26T08:00:00Z",
  };
}
