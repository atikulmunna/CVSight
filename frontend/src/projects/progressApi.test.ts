import { describe, expect, it, vi } from "vitest";

import { loadProjectProgress, ProgressApiError } from "./progressApi";

const VERSION_ID = "22222222-2222-4222-8222-222222222222";

describe("project progress API", () => {
  it("loads and parses version progress", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, progressBody()));

    await expect(loadProjectProgress(VERSION_ID, fetcher)).resolves.toEqual({
      datasetVersionId: VERSION_ID,
      images: { total: 10 },
      annotations: { total: 20, decided: 15 },
      identity: {
        acceptedProducts: 12,
        knownProducts: 9,
        unknownProducts: 2,
        unassignedProducts: 1,
      },
      qa: { reviewedImages: 4, flaggedAnnotations: 1 },
    });
    expect(fetcher).toHaveBeenCalledWith(
      `/api/dataset-versions/${VERSION_ID}/progress`,
      { headers: { Accept: "application/json" } },
    );
  });

  it("rejects malformed progress responses", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(200, { ...progressBody(), dataset_version_id: "unsafe" }),
    );

    await expect(loadProjectProgress(VERSION_ID, fetcher)).rejects.toEqual(
      new ProgressApiError(502, "invalid_response"),
    );
  });

  it("rejects contradictory progress totals", async () => {
    const body = progressBody();
    body.identity.known_products = 12;
    const fetcher = vi.fn().mockResolvedValue(response(200, body));

    await expect(loadProjectProgress(VERSION_ID, fetcher)).rejects.toEqual(
      new ProgressApiError(502, "invalid_response"),
    );
  });

  it("preserves stable server error codes", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(404, { detail: { code: "dataset_version_not_found" } }),
    );

    await expect(loadProjectProgress(VERSION_ID, fetcher)).rejects.toEqual(
      new ProgressApiError(404, "dataset_version_not_found"),
    );
  });
});

function progressBody() {
  return {
    dataset_version_id: VERSION_ID,
    images: { total: 10 },
    annotations: { total: 20, decided: 15 },
    identity: {
      accepted_products: 12,
      known_products: 9,
      unknown_products: 2,
      unassigned_products: 1,
    },
    qa: { reviewed_images: 4, flagged_annotations: 1 },
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}
