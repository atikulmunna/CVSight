import { describe, expect, it, vi } from "vitest";

import {
  loadReviewQueue,
  loadSkuLineup,
  ReviewApiError,
  reviewItemCropUrl,
  signOffReview,
  submitReviewDecision,
} from "./api";

const VERSION_ID = "11111111-1111-4111-8111-111111111111";
const ANNOTATION_ID = "22222222-2222-4222-8222-222222222222";
const IMAGE_ID = "33333333-3333-4333-8333-333333333333";
const SKU_ID = "44444444-4444-4444-8444-444444444444";
const REFERENCE_ID = "77777777-7777-4777-8777-777777777777";

describe("review API", () => {
  it("parses visible risk reasons and submits revision-safe decisions", async () => {
    const fetcher = vi.fn(async () => response(queuePayload()));
    const queue = await loadReviewQueue(VERSION_ID, fetcher);

    expect(queue.items[0]?.riskReasons.map((reason) => reason.code)).toEqual([
      "hard_pair_confusion",
      "propagated_origin",
    ]);
    expect(queue.unresolvedCount).toBe(1);

    let capturedRequest: RequestInit | undefined;
    const decisionFetcher: typeof fetch = async (_input, request) => {
      capturedRequest = request;
      return response({ annotation: {}, decision: {} });
    };
    await submitReviewDecision(
      VERSION_ID,
      queue.items[0]!,
      "approve",
      decisionFetcher,
    );
    expect(capturedRequest?.method).toBe("POST");
    expect(capturedRequest?.headers).not.toHaveProperty("X-ShelfSight-Actor");
    expect(JSON.parse(String(capturedRequest?.body))).toEqual({
      expected_revision: 3,
      decision: "approve",
      note: null,
    });
  });

  it("rejects malformed projection paths and preserves stable API errors", async () => {
    const malformed = queuePayload();
    malformed.items[0]!.image_url = "https://example.com/private.jpg";
    await expect(
      loadReviewQueue(VERSION_ID, async () => response(malformed)),
    ).rejects.toMatchObject({ code: "invalid_response" });

    await expect(
      signOffReview(
        VERSION_ID,
        async () => response({ detail: { code: "review_signoff_blocked" } }, 409),
      ),
    ).rejects.toEqual(new ReviewApiError(409, "review_signoff_blocked"));
  });

  it("loads a SKU lineup and builds the crop of the revision under review", async () => {
    const fetcher = vi.fn(async () => response(lineupPayload()));
    const lineup = await loadSkuLineup(VERSION_ID, SKU_ID, fetcher);

    expect(fetcher).toHaveBeenCalledWith(
      `/api/dataset-versions/${VERSION_ID}/sku-lineup?sku_id=${SKU_ID}`,
      { headers: { Accept: "application/json" } },
    );
    expect(lineup).toEqual({
      skuId: SKU_ID,
      skuName: "Lineup Cola 330 ml",
      references: [
        {
          id: REFERENCE_ID,
          fileName: "cola-front.jpg",
          thumbnailUrl: `/api/sku-reference-images/${REFERENCE_ID}/media/thumbnail`,
        },
      ],
      items: [
        {
          annotationId: ANNOTATION_ID,
          revision: 3,
          imageId: IMAGE_ID,
          imageName: "review-shelf.jpg",
          cropUrl: `/api/annotations/${ANNOTATION_ID}/crop?revision=3`,
        },
      ],
      truncated: false,
    });

    const queue = await loadReviewQueue(VERSION_ID, async () => response(queuePayload()));
    expect(reviewItemCropUrl(queue.items[0]!)).toBe(
      `/api/annotations/${ANNOTATION_ID}/crop?revision=3`,
    );
  });

  it("refuses malformed lineups, invalid ids, and keeps stable lineup errors", async () => {
    const outside = lineupPayload();
    outside.items[0]!.crop_url = "https://example.com/crop.jpg";
    await expect(
      loadSkuLineup(VERSION_ID, SKU_ID, async () => response(outside)),
    ).rejects.toMatchObject({ code: "invalid_response" });

    const unsafeReference = lineupPayload();
    unsafeReference.sku.reference_images[0]!.thumbnail_url = "/api\\..\\private.jpg";
    await expect(
      loadSkuLineup(VERSION_ID, SKU_ID, async () => response(unsafeReference)),
    ).rejects.toMatchObject({ code: "invalid_response" });

    await expect(
      loadSkuLineup(VERSION_ID, SKU_ID, async () => response({ ...lineupPayload(), items: null })),
    ).rejects.toMatchObject({ code: "invalid_response" });

    const fetcher = vi.fn();
    await expect(loadSkuLineup(VERSION_ID, "not-a-sku", fetcher)).rejects.toEqual(
      new ReviewApiError(400, "invalid_id"),
    );
    expect(fetcher).not.toHaveBeenCalled();

    await expect(
      loadSkuLineup(
        VERSION_ID,
        SKU_ID,
        async () => response({ detail: { code: "sku_not_found", message: "SKU does not exist" } }, 404),
      ),
    ).rejects.toEqual(new ReviewApiError(404, "sku_not_found"));
  });
});

function lineupPayload() {
  return {
    dataset_version_id: VERSION_ID,
    sku: {
      id: SKU_ID,
      name: "Lineup Cola 330 ml",
      reference_images: [
        {
          id: REFERENCE_ID,
          original_filename: "cola-front.jpg",
          thumbnail_url: `/api/sku-reference-images/${REFERENCE_ID}/media/thumbnail`,
        },
      ],
    },
    items: [
      {
        annotation_id: ANNOTATION_ID,
        revision: 3,
        image_id: IMAGE_ID,
        image_name: "review-shelf.jpg",
        crop_url: `/api/annotations/${ANNOTATION_ID}/crop?revision=3`,
      },
    ],
    truncated: false,
  };
}

function queuePayload() {
  return {
    dataset_version_id: VERSION_ID,
    status: "open",
    total_risk_items: 1,
    unresolved_count: 1,
    resolved_count: 0,
    signoff: null,
    items: [
      {
        annotation_id: ANNOTATION_ID,
        annotation_revision: 3,
        image_id: IMAGE_ID,
        image_name: "review-shelf.jpg",
        image_url: `/api/images/${IMAGE_ID}/media/canonical`,
        risk_score: 160,
        resolved: false,
        risk_reasons: [
          {
            code: "hard_pair_confusion",
            label: "Fine-grained hard-pair confusion",
            weight: 100,
            blocking: true,
          },
          {
            code: "propagated_origin",
            label: "Label originated from propagation",
            weight: 60,
            blocking: true,
          },
        ],
      },
    ],
  };
}

function response(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
