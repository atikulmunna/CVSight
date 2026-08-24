import { describe, expect, it, vi } from "vitest";

import { LiveFixtureError, loadLiveFixture } from "./liveFixture";

const IMAGE_ID = "11111111-1111-4111-8111-111111111111";
const ANNOTATION_ID = "22222222-2222-4222-8222-222222222222";

describe("loadLiveFixture", () => {
  it("maps live image metadata and current annotation revisions", async () => {
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) => {
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
              sku_id: "33333333-3333-4333-8333-333333333333",
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
      sku: "Assigned SKU",
      truncated: true,
      shelfRow: 1,
    });
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
