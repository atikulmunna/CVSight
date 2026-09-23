import { describe, expect, it, vi } from "vitest";

import { LiveFixtureError, loadLiveFixture } from "./liveFixture";

const IMAGE_ID = "11111111-1111-4111-8111-111111111111";
const ANNOTATION_ID = "22222222-2222-4222-8222-222222222222";
const SKU_ID = "33333333-3333-4333-8333-333333333333";

describe("loadLiveFixture", () => {
  it("maps live image metadata and current annotation revisions", async () => {
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      if (String(input) === `/api/skus/${SKU_ID}`) {
        return Promise.resolve(response({ id: SKU_ID, name: "Horlicks Standard" }));
      }
      if (String(input) === `/api/images/${IMAGE_ID}`) {
        return Promise.resolve(
          response({
            id: IMAGE_ID,
            original_filename: "live-shelf.jpg",
            width: 120,
            height: 80,
            canonical_url: `/api/images/${IMAGE_ID}/media/canonical`,
          }),
        );
      }
      return Promise.resolve(
        response({
          annotations: [
            {
              id: ANNOTATION_ID,
              image_id: IMAGE_ID,
              revision: 3,
              x: 10,
              y: 12,
              width: 30,
              height: 40,
              class_type: "product",
              lifecycle_state: "verified",
              review_state: "accepted",
              source: "human",
              sku_id: SKU_ID,
              confidence: 0.9,
              occluded: false,
              truncated: true,
              shelf_row: 1,
            },
          ],
        }),
      );
    });

    const fixture = await loadLiveFixture(IMAGE_ID, fetcher);

    expect(fixture).toMatchObject({
      name: "live-shelf.jpg",
      width: 120,
      height: 80,
    });
    expect(fixture.annotations[0]).toMatchObject({
      id: ANNOTATION_ID,
      serverId: ANNOTATION_ID,
      imageId: IMAGE_ID,
      revision: 3,
      state: "verified",
      sku: "Horlicks Standard",
      truncated: true,
      shelfRow: 1,
    });
  });

  it("looks up each distinct SKU name once and shows a failed lookup", async () => {
    const missingSku = "44444444-4444-4444-8444-444444444444";
    const box = (id: string, skuId: string | null) => ({
      id,
      image_id: IMAGE_ID,
      revision: 1,
      x: 10,
      y: 12,
      width: 30,
      height: 40,
      class_type: "product",
      lifecycle_state: "proposed",
      review_state: "unreviewed",
      source: "model",
      sku_id: skuId,
      confidence: 0.9,
      occluded: false,
      truncated: false,
      shelf_row: 0,
    });
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `/api/skus/${SKU_ID}`) {
        return Promise.resolve(response({ id: SKU_ID, name: "Horlicks Chocolate" }));
      }
      if (url === `/api/skus/${missingSku}`) {
        return Promise.resolve(response({ detail: { code: "sku_not_found" } }, 404));
      }
      if (url === `/api/images/${IMAGE_ID}`) {
        return Promise.resolve(
          response({
            id: IMAGE_ID,
            original_filename: "pre-labeled.jpg",
            width: 120,
            height: 80,
            canonical_url: `/api/images/${IMAGE_ID}/media/canonical`,
          }),
        );
      }
      return Promise.resolve(
        response({
          annotations: [
            box("55555555-5555-4555-8555-555555555551", SKU_ID),
            box("55555555-5555-4555-8555-555555555552", SKU_ID),
            box("55555555-5555-4555-8555-555555555553", missingSku),
            box("55555555-5555-4555-8555-555555555554", null),
          ],
        }),
      );
    });

    const fixture = await loadLiveFixture(IMAGE_ID, fetcher);

    expect(fixture.annotations.map((annotation) => annotation.sku)).toEqual([
      "Horlicks Chocolate",
      "Horlicks Chocolate",
      "SKU name unavailable",
      "Unknown SKU",
    ]);
    const skuRequests = fetcher.mock.calls.filter(([input]) =>
      String(input).startsWith("/api/skus/"),
    );
    expect(skuRequests).toHaveLength(2);
  });

  it("rejects invalid identifiers before making a request", async () => {
    const fetcher = vi.fn();

    await expect(loadLiveFixture("../private", fetcher)).rejects.toMatchObject({
      status: 400,
      code: "invalid_image_id",
    } satisfies Partial<LiveFixtureError>);
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("rejects annotations returned for a different image", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        response({
          id: IMAGE_ID,
          original_filename: "live-shelf.jpg",
          width: 120,
          height: 80,
          canonical_url: `/api/images/${IMAGE_ID}/media/canonical`,
        }),
      )
      .mockResolvedValueOnce(
        response({
          annotations: [
            {
              id: ANNOTATION_ID,
              image_id: "44444444-4444-4444-8444-444444444444",
            },
          ],
        }),
      );

    await expect(loadLiveFixture(IMAGE_ID, fetcher)).rejects.toMatchObject({
      status: 502,
      code: "invalid_response",
    } satisfies Partial<LiveFixtureError>);
  });

  it("preserves stable API error codes", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(
        response(
          {
            detail: {
              code: "image_not_found",
              message: "image does not exist",
            },
          },
          404,
        ),
      )
      .mockResolvedValueOnce(response({ annotations: [] }));

    await expect(loadLiveFixture(IMAGE_ID, fetcher)).rejects.toMatchObject({
      status: 404,
      code: "image_not_found",
    } satisfies Partial<LiveFixtureError>);
  });
});

function response(body: unknown, status = 200): Response {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  } as Response;
}
