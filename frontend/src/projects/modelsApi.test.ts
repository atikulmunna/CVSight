import { describe, expect, it, vi } from "vitest";

import {
  loadModelCandidates,
  loadModelDeployment,
  ModelsApiError,
  promoteModel,
  rollbackModel,
} from "./modelsApi";

const ENTRY_ID = "11111111-1111-4111-8111-111111111111";
const ARTIFACT_ID = "22222222-2222-4222-8222-222222222222";
const VERSION_ID = "33333333-3333-4333-8333-333333333333";

function entry(overrides: Record<string, unknown> = {}) {
  return {
    id: ENTRY_ID,
    model_role: "known_sku_detector",
    model_id: "shelf-detector",
    model_version: "2026.09-cycle-1",
    model_artifact_id: ARTIFACT_ID,
    evaluation_artifact_id: ARTIFACT_ID,
    model_artifact_key: "artifacts/model.pth",
    evaluation_artifact_key: "artifacts/evaluation.json",
    lineage: "snapshot",
    source: null,
    training_dataset_version_id: VERSION_ID,
    evaluation_dataset_version_id: VERSION_ID,
    model_artifact_sha256: "a".repeat(64),
    evaluation_artifact_sha256: "b".repeat(64),
    configuration: { epochs: 12 },
    compatibility: { runtime: "detector-runtime-1" },
    metrics: { map_50_95: 0.612, product_recall_at_iou_50: 0.93, notes: "ignored" },
    registered_by: "owner:owner",
    registered_at: "2026-09-22T08:00:00Z",
    deployment_status: "candidate",
    ...overrides,
  };
}

function response(status: number, body: unknown) {
  return { ok: status >= 200 && status < 300, status, json: async () => body };
}

describe("models API", () => {
  it("reads an externally trained model's source instead of a training version", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(200, {
        models: [
          entry({
            lineage: "external",
            source: "Two-stage shelf detector",
            training_dataset_version_id: null,
          }),
        ],
      }),
    );

    const [candidate] = await loadModelCandidates("known_sku_detector", fetcher);

    expect(candidate).toMatchObject({
      lineage: "external",
      source: "Two-stage shelf detector",
      trainingDatasetVersionId: null,
    });
  });

  it("refuses an entry whose lineage does not add up", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(200, { models: [entry({ lineage: "external", source: null })] }),
    );

    await expect(loadModelCandidates("known_sku_detector", fetcher)).rejects.toMatchObject({
      code: "invalid_response",
    });
  });

  it("loads candidates and keeps only numeric metrics", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, { models: [entry()] }));

    const [candidate] = await loadModelCandidates("known_sku_detector", fetcher);

    expect(fetcher).toHaveBeenCalledWith("/api/model-registry?model_role=known_sku_detector", {
      headers: { Accept: "application/json" },
    });
    expect(candidate).toMatchObject({
      id: ENTRY_ID,
      modelId: "shelf-detector",
      modelVersion: "2026.09-cycle-1",
      deploymentStatus: "candidate",
      metrics: { map_50_95: 0.612, product_recall_at_iou_50: 0.93 },
    });
  });

  it("treats a missing deployment as an ordinary empty state", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(404, { detail: { code: "model_deployment_not_found", message: "none" } }),
    );

    await expect(loadModelDeployment("known_sku_detector", fetcher)).resolves.toBeNull();
  });

  it("parses a deployment with its previous entry", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, {
      model_role: "known_sku_detector",
      active: entry({ deployment_status: "default" }),
      previous: entry({ id: VERSION_ID, model_version: "2026.08", deployment_status: "previous" }),
      updated_by: "owner:owner",
      updated_at: "2026-09-22T09:00:00Z",
    }));

    const deployment = await loadModelDeployment("known_sku_detector", fetcher);

    expect(deployment?.active.deploymentStatus).toBe("default");
    expect(deployment?.previous?.modelVersion).toBe("2026.08");
    expect(deployment?.updatedBy).toBe("owner:owner");
  });

  it("sends the expected active identifier when promoting and rolling back", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, {
      model_role: "known_sku_detector",
      active: entry({ deployment_status: "default" }),
      previous: null,
      updated_by: "owner:owner",
      updated_at: "2026-09-22T09:00:00Z",
    }));

    await promoteModel("known_sku_detector", ENTRY_ID, null, fetcher);
    await rollbackModel("known_sku_detector", ENTRY_ID, fetcher);

    expect(fetcher).toHaveBeenNthCalledWith(1, "/api/model-deployments/known_sku_detector/promote", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ entry_id: ENTRY_ID, expected_active_id: null }),
    }));
    expect(fetcher).toHaveBeenNthCalledWith(2, "/api/model-deployments/known_sku_detector/rollback", expect.objectContaining({
      method: "POST",
      body: JSON.stringify({ expected_active_id: ENTRY_ID }),
    }));
  });

  it("preserves stable error codes and rejects malformed entries", async () => {
    const stale = vi.fn().mockResolvedValue(
      response(409, { detail: { code: "stale_model_deployment", message: "hidden" } }),
    );
    await expect(promoteModel("known_sku_detector", ENTRY_ID, null, stale)).rejects.toEqual(
      new ModelsApiError(409, "stale_model_deployment"),
    );

    const malformed = vi.fn().mockResolvedValue(
      response(200, { models: [entry({ deployment_status: "unknown" })] }),
    );
    await expect(loadModelCandidates("known_sku_detector", malformed)).rejects.toEqual(
      new ModelsApiError(502, "invalid_response"),
    );
  });
});
