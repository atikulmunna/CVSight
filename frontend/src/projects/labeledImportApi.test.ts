import { describe, expect, it, vi } from "vitest";

import { ProjectImageApiError } from "./imagesApi";
import { previewLabeledImport, runLabeledImportPage } from "./labeledImportApi";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";
const SKU_ID = "33333333-3333-4333-8333-333333333333";
const BASE = `/api/datasets/${PROJECT_ID}/versions/${VERSION_ID}/labeled-imports`;

describe("labeled import API", () => {
  it("previews classes, counts, and source splits", async () => {
    const fetcher = vi.fn().mockReturnValue(
      json(200, {
        format: "yolo",
        images: 2,
        boxes: 3,
        skipped_labels: 1,
        classes: [{ name: "cola", boxes: 2, images: 2, suggested_sku_id: SKU_ID }],
        source_splits: { train: 1, validation: 1 },
      }),
    );

    const preview = await previewLabeledImport(PROJECT_ID, VERSION_ID, "yolo", "shelf", fetcher);

    expect(preview).toEqual({
      images: 2,
      boxes: 3,
      skippedLabels: 1,
      classes: [{ name: "cola", boxes: 2, images: 2, suggestedSkuId: SKU_ID }],
      sourceSplits: { train: 1, validation: 1 },
    });
    expect(fetcher).toHaveBeenCalledWith(
      `${BASE}/preview`,
      expect.objectContaining({ method: "POST", body: JSON.stringify({ format: "yolo", path: "shelf" }) }),
    );
  });

  it("sends one page of the import and reads where to continue", async () => {
    const fetcher = vi.fn().mockReturnValue(
      json(200, {
        results: [{ path: "shelf/a.jpg", outcome: "imported", code: "image_imported", image_id: null, boxes: 2 }],
        next_offset: 100,
        total_images: 250,
      }),
    );

    const page = await runLabeledImportPage(
      PROJECT_ID,
      VERSION_ID,
      { format: "coco", path: "set.json", classSkus: { cola: SKU_ID }, annotationState: "proposed", offset: 0 },
      fetcher,
    );

    expect(page).toEqual({
      results: [{ path: "shelf/a.jpg", outcome: "imported", code: "image_imported", boxes: 2 }],
      nextOffset: 100,
      totalImages: 250,
    });
    const sent = JSON.parse(String(fetcher.mock.calls[0]![1].body)) as Record<string, unknown>;
    expect(sent).toEqual({
      format: "coco",
      path: "set.json",
      class_skus: { cola: SKU_ID },
      annotation_state: "proposed",
      offset: 0,
    });
  });

  it("surfaces the API's error code", async () => {
    const fetcher = vi.fn().mockReturnValue(json(400, { detail: { code: "missing_data_yaml" } }));

    await expect(previewLabeledImport(PROJECT_ID, VERSION_ID, "yolo", "shelf", fetcher)).rejects.toEqual(
      new ProjectImageApiError(400, "missing_data_yaml"),
    );
  });

  it("rejects an outcome it does not know", async () => {
    const fetcher = vi.fn().mockReturnValue(
      json(200, {
        results: [{ path: "a.jpg", outcome: "exploded", code: "x", image_id: null, boxes: 0 }],
        next_offset: null,
        total_images: 1,
      }),
    );

    await expect(
      runLabeledImportPage(
        PROJECT_ID,
        VERSION_ID,
        { format: "yolo", path: "shelf", classSkus: {}, annotationState: "verified", offset: 0 },
        fetcher,
      ),
    ).rejects.toMatchObject({ code: "invalid_response" });
  });
});

function json(status: number, body: unknown) {
  return Promise.resolve({ ok: status >= 200 && status < 300, status, json: async () => body });
}
