import { describe, expect, it, vi } from "vitest";

import { AnnotationSaveError, saveAnnotation } from "./annotationApi";
import type { AnnotationBox } from "./model";

const BOX: AnnotationBox = {
  id: "local-1",
  serverId: "server-1",
  imageId: "image-1",
  revision: 4,
  x: 10,
  y: 12,
  width: 20,
  height: 30,
  classType: "product",
  state: "flagged",
  lifecycleState: "proposed",
  reviewState: "flagged",
  sku: "Test SKU",
  skuId: null,
  confidence: 0.8,
  occluded: false,
  truncated: false,
  shelfRow: 1,
  imageIndex: 0,
};

describe("annotation API client", () => {
  it("sends accept with the expected revision and maps the new state", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(200, annotationResponse({
        revision: 5,
        lifecycle_state: "verified",
        review_state: "accepted",
      })),
    );

    await expect(saveAnnotation(BOX, "accept", fetcher)).resolves.toMatchObject({
      id: "local-1",
      serverId: "server-1",
      revision: 5,
      state: "verified",
      lifecycleState: "verified",
      reviewState: "accepted",
      sku: "Test SKU",
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/annotations/server-1/accept",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ expected_revision: 4 }),
      }),
    );
  });

  it("sends complete annotation fields for an update", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(response(200, annotationResponse()));

    await saveAnnotation(BOX, "update", fetcher);

    const options = fetcher.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(options.body as string)).toMatchObject({
      expected_revision: 4,
      x: 10,
      review_state: "flagged",
      class_type: "product",
    });
  });

  it("sends SKU assignment through the dedicated audited action", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(
        200,
        annotationResponse({
          revision: 5,
          lifecycle_state: "verified",
          review_state: "accepted",
          sku_id: "sku-2",
        }),
      ),
    );
    const assigned = {
      ...BOX,
      lifecycleState: "verified" as const,
      reviewState: "accepted" as const,
      skuId: "sku-2",
      sku: "Second SKU",
    };

    await expect(saveAnnotation(assigned, "assign", fetcher)).resolves.toMatchObject({
      revision: 5,
      skuId: "sku-2",
      sku: "Second SKU",
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/annotations/server-1/assign-sku",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({
          expected_revision: 4,
          sku_id: "sku-2",
        }),
      }),
    );
  });

  it("returns stable save errors without exposing response details", async () => {
    const fetcher = vi.fn().mockResolvedValue(
      response(409, {
        detail: {
          code: "stale_revision",
          message: "sensitive server message",
        },
      }),
    );

    const error = await saveAnnotation(BOX, "update", fetcher).catch(
      (caught: unknown) => caught,
    );

    expect(error).toBeInstanceOf(AnnotationSaveError);
    expect(error).toMatchObject({ status: 409, code: "stale_revision" });
    expect((error as Error).message).not.toContain("sensitive");
  });

  it("rejects malformed successful responses", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(response(200, { ...annotationResponse(), x: -1 }));

    await expect(saveAnnotation(BOX, "update", fetcher)).rejects.toMatchObject({
      status: 502,
      code: "invalid_x",
    });
  });
});

function annotationResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: "server-1",
    image_id: "image-1",
    revision: 4,
    x: 10,
    y: 12,
    width: 20,
    height: 30,
    class_type: "product",
    sku_id: null,
    lifecycle_state: "proposed",
    review_state: "flagged",
    source: "human",
    confidence: 0.8,
    occluded: false,
    truncated: false,
    shelf_row: 1,
    ...overrides,
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}
