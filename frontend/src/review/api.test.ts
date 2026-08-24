import { describe, expect, it, vi } from "vitest";

import {
  loadReviewQueue,
  ReviewApiError,
  signOffReview,
  submitReviewDecision,
} from "./api";

const VERSION_ID = "11111111-1111-4111-8111-111111111111";
const ANNOTATION_ID = "22222222-2222-4222-8222-222222222222";
const IMAGE_ID = "33333333-3333-4333-8333-333333333333";

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
});

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
