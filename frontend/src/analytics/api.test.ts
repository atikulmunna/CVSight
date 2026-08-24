import { describe, expect, it, vi } from "vitest";

import {
  AnalyticsApiError,
  analyticsExportUrl,
  loadAnalytics,
  parseAnalyticsReport,
} from "./api";

const VERSION_ID = "11111111-1111-4111-8111-111111111111";

describe("analytics API", () => {
  it("loads and validates a snapshot report", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: true,
      json: async () => response(),
    });

    const report = await loadAnalytics(VERSION_ID, fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      `/api/dataset-versions/${VERSION_ID}/analytics`,
      { headers: { Accept: "application/json" } },
    );
    expect(report.summary.acceptedProductFacings).toBe(2);
    expect(report.images[0]!.countShare.shares).toEqual({
      "sku-a": 0.5,
      unknown: 0.5,
    });
    expect(analyticsExportUrl(VERSION_ID, "csv").endsWith(
      "/analytics/export?format=csv",
    )).toBe(true);
  });

  it("preserves stable API error codes", async () => {
    const fetcher = vi.fn().mockResolvedValue({
      ok: false,
      status: 409,
      json: async () => ({
        detail: { code: "dataset_version_not_frozen", message: "not frozen" },
      }),
    });

    await expect(loadAnalytics(VERSION_ID, fetcher)).rejects.toEqual(
      new AnalyticsApiError(409, "dataset_version_not_frozen"),
    );
  });

  it("rejects malformed metrics and identifiers", () => {
    expect(() =>
      parseAnalyticsReport({
        ...response(),
        result_sha256: "not-a-hash",
      }),
    ).toThrowError(AnalyticsApiError);
    expect(() => analyticsExportUrl("not-a-uuid", "json")).toThrowError(
      AnalyticsApiError,
    );
    expect(() =>
      parseAnalyticsReport({
        ...response(),
        images: [
          {
            ...response().images[0],
            count_share: {
              ...response().images[0]!.count_share,
              shares: { "sku-a": 1.2 },
            },
          },
        ],
      }),
    ).toThrowError(AnalyticsApiError);
  });
});

export function response() {
  return {
    schema_version: "shelfsight-analytics-response/v1",
    dataset_id: "22222222-2222-4222-8222-222222222222",
    dataset_version_id: VERSION_ID,
    snapshot: {
      schema_version: "shelfsight-dataset-snapshot/v2",
      content_sha256: "a".repeat(64),
      snapshot_at: "2026-08-24T12:00:00+00:00",
    },
    formula_version: "shelfsight-retail-analytics/v1",
    result_sha256: "b".repeat(64),
    generated_at: "2026-08-24T13:00:00+00:00",
    model: { basis: "verified_snapshot_annotations", required: false },
    group_by: "sku",
    group_labels: { "sku-a": "Cola", unknown: "Unknown" },
    summary: {
      images: 1,
      reviewed_images: 1,
      active_product_facings: 2,
      accepted_product_facings: 2,
      accepted_gaps: 1,
      final_count_share_images: 1,
    },
    planogram: {
      status: "unsupported",
      is_final: false,
      reason: "no_planogram_reference",
      compliance_rate: null,
    },
    images: [
      {
        image_id: "33333333-3333-4333-8333-333333333333",
        image_name: "shelf.jpg",
        image_status: "reviewed",
        capture_view: "frontal",
        active_product_facings: 2,
        accepted_product_facings: 2,
        count_share: {
          status: "complete",
          is_final: true,
          reason: null,
          shares: { "sku-a": 0.5, unknown: 0.5 },
        },
        image_area_share: {
          status: "complete",
          is_final: true,
          reason: null,
          shares: { "sku-a": 0.5, unknown: 0.5 },
        },
        realogram: { status: "complete", is_final: true },
        gaps: { status: "complete", is_final: true, accepted_count: 1 },
      },
    ],
  };
}
