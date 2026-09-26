import { describe, expect, it, vi } from "vitest";

import {
  createWorkingVersion,
  loadProjectVersions,
  loadWorkingVersionReview,
  ProjectVersionsApiError,
  versionExportUrl,
} from "./versionsApi";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const WORKING_ID = "22222222-2222-4222-8222-222222222222";
const RELEASE_ID = "33333333-3333-4333-8333-333333333333";

describe("project versions API", () => {
  it("loads working and signed immutable versions", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, versionBody()));

    const versions = await loadProjectVersions(PROJECT_ID, fetcher);

    expect(versions).toHaveLength(2);
    expect(versions[0]).toMatchObject({
      id: WORKING_ID,
      status: "working",
      imageCount: 24,
      reviewSignoff: null,
    });
    expect(versions[1]).toMatchObject({
      id: RELEASE_ID,
      status: "released",
      exportTypes: ["detection", "yolo"],
      reviewSignoff: {
        signedBy: "reviewer:qa",
        reviewedAnnotationCount: 180,
        riskItemCount: 12,
      },
    });
  });

  it("loads current review counts and creates the next working version", async () => {
    const reviewFetcher = vi.fn().mockResolvedValue(response(200, {
      total_risk_items: 12,
      unresolved_count: 2,
      resolved_count: 10,
    }));
    const createFetcher = vi.fn().mockResolvedValue(response(201, { id: WORKING_ID }));

    await expect(loadWorkingVersionReview(WORKING_ID, reviewFetcher)).resolves.toEqual({
      totalRiskItems: 12,
      unresolvedCount: 2,
      resolvedCount: 10,
    });
    await expect(createWorkingVersion(PROJECT_ID, createFetcher)).resolves.toBe(WORKING_ID);
    expect(createFetcher).toHaveBeenCalledWith(`/api/datasets/${PROJECT_ID}/versions`, {
      method: "POST",
      headers: { Accept: "application/json" },
    });
  });

  it("builds bounded immutable export URLs", () => {
    expect(versionExportUrl(RELEASE_ID, "recognition")).toBe(
      `/api/dataset-versions/${RELEASE_ID}/exports/recognition`,
    );
    expect(() => versionExportUrl("unsafe", "detection")).toThrow(
      new ProjectVersionsApiError(502, "invalid_response"),
    );
  });

  it("rejects contradictory and malformed version responses", async () => {
    const contradictory = versionBody();
    contradictory[0]!.snapshot_at = "2026-08-31T08:00:00Z";
    const fetcher = vi.fn().mockResolvedValue(response(200, contradictory));

    await expect(loadProjectVersions(PROJECT_ID, fetcher)).rejects.toEqual(
      new ProjectVersionsApiError(502, "invalid_response"),
    );

    const reviewFetcher = vi.fn().mockResolvedValue(response(200, {
      total_risk_items: 4,
      unresolved_count: 3,
      resolved_count: 2,
    }));
    await expect(loadWorkingVersionReview(WORKING_ID, reviewFetcher)).rejects.toEqual(
      new ProjectVersionsApiError(502, "invalid_response"),
    );
  });

  it("preserves stable server error codes", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(409, { detail: { code: "dataset_open_version_exists" } }),
    );

    await expect(createWorkingVersion(PROJECT_ID, fetcher)).rejects.toEqual(
      new ProjectVersionsApiError(409, "dataset_open_version_exists"),
    );
  });
});

function versionBody() {
  return [
    {
      id: WORKING_ID,
      dataset_id: PROJECT_ID,
      parent_version_id: RELEASE_ID,
      created_at: "2026-09-01T08:00:00Z",
      snapshot_at: null as string | null,
      status: "working",
      image_count: 24,
      review_signoff: null,
      export_types: [],
    },
    {
      id: RELEASE_ID,
      dataset_id: PROJECT_ID,
      parent_version_id: null,
      created_at: "2026-08-30T08:00:00Z",
      snapshot_at: "2026-08-31T08:00:00Z",
      status: "released",
      image_count: 24,
      review_signoff: {
        signed_by: "reviewer:qa",
        signed_at: "2026-08-31T08:00:00Z",
        reviewed_annotation_count: 180,
        risk_item_count: 12,
      },
      export_types: ["detection", "yolo"],
    },
  ];
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  };
}
