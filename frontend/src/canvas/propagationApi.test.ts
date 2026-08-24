import { describe, expect, it, vi } from "vitest";

import type { AnnotationBox } from "./model";
import {
  confirmPropagationSuggestions,
  createPropagationSuggestions,
  PropagationApiError,
} from "./propagationApi";

const SEED: AnnotationBox = {
  id: "seed-local",
  serverId: "seed-server",
  imageId: "image-1",
  revision: 3,
  x: 1,
  y: 2,
  width: 20,
  height: 30,
  classType: "product",
  state: "verified",
  lifecycleState: "verified",
  reviewState: "accepted",
  sku: "Seed SKU",
  skuId: "sku-1",
  confidence: null,
  occluded: false,
  truncated: false,
  shelfRow: 0,
  imageIndex: 0,
};

const CANDIDATE: AnnotationBox = {
  ...SEED,
  id: "candidate-local",
  serverId: "candidate-server",
  revision: 1,
  x: 30,
  sku: "Unknown SKU",
  skuId: null,
};

describe("propagation API client", () => {
  it("creates an explicitly unselected suggestion set", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(201, suggestionResponse()));

    const result = await createPropagationSuggestions(SEED, fetcher);

    expect(result.candidates).toHaveLength(1);
    expect(result.candidates[0]).toMatchObject({
      suggestionId: "suggestion-1",
      requiresIndividualReview: true,
      riskReason: "hard_pair",
    });
    const init = fetcher.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(init.body as string)).toEqual({
      expected_revision: 3,
      top_k: 18,
    });
  });

  it("confirms selected crops and maps propagated server revisions", async () => {
    const suggestionFetcher = vi
      .fn()
      .mockResolvedValue(response(201, suggestionResponse()));
    const suggestionSet = await createPropagationSuggestions(
      SEED,
      suggestionFetcher,
    );
    const confirmFetcher = vi.fn().mockResolvedValue(
      response(200, {
        suggestion_set_id: "set-1",
        selected_count: 1,
        skipped_count: 0,
        annotations: [
          annotationResponse({
            id: "candidate-server",
            revision: 2,
            sku_id: "sku-1",
            source: "propagated",
          }),
        ],
      }),
    );

    const result = await confirmPropagationSuggestions(
      suggestionSet,
      new Set(["suggestion-1"]),
      new Set(["suggestion-1"]),
      [SEED, CANDIDATE],
      confirmFetcher,
    );

    expect(result.annotations[0]).toMatchObject({
      id: "candidate-local",
      revision: 2,
      sku: "Seed SKU",
      skuId: "sku-1",
      state: "verified",
    });
    const init = confirmFetcher.mock.calls[0]![1] as RequestInit;
    expect(JSON.parse(init.body as string).decisions).toEqual([
      {
        suggestion_id: "suggestion-1",
        decision: "confirm",
        reviewed_individually: true,
      },
    ]);
  });

  it("rejects unsafe crop URLs and preserves stable API error codes", async () => {
    const malformed = vi.fn().mockResolvedValue(
      response(201, {
        ...suggestionResponse(),
        candidates: [
          {
            ...suggestionResponse().candidates[0],
            image_url: "https://example.com/private.jpg",
          },
        ],
      }),
    );
    await expect(
      createPropagationSuggestions(SEED, malformed),
    ).rejects.toMatchObject({
      status: 502,
      code: "invalid_candidate_image_url",
    });

    const failed = vi.fn().mockResolvedValue(
      response(409, {
        detail: {
          code: "propagation_index_not_current",
          message: "internal details",
        },
      }),
    );
    const error = await createPropagationSuggestions(SEED, failed).catch(
      (caught: unknown) => caught,
    );
    expect(error).toBeInstanceOf(PropagationApiError);
    expect(error).toMatchObject({
      status: 409,
      code: "propagation_index_not_current",
    });
    expect((error as Error).message).not.toContain("internal");
  });
});

function suggestionResponse() {
  return {
    suggestion_set_id: "set-1",
    seed_annotation_id: "seed-server",
    seed_annotation_revision: 3,
    seed_sku: {
      sku_id: "sku-1",
      name: "Seed SKU",
      brand: "Shared",
      variant: "Large",
    },
    index: {
      namespace: "propagation:test",
      version: "1",
      purpose: "propagation",
      quality_evidence: "test-evidence",
    },
    hard_pairs: [
      {
        sku_id: "sku-2",
        name: "Confusable SKU",
        brand: "Shared",
        variant: "Small",
        reason: "same brand, different size",
      },
    ],
    candidates: [
      {
        suggestion_id: "suggestion-1",
        annotation_id: "candidate-server",
        annotation_revision: 1,
        image_id: "image-1",
        image_url: "/api/images/image-1/media/canonical",
        image_width: 100,
        image_height: 80,
        x: 30,
        y: 2,
        width: 20,
        height: 30,
        score: 0.93,
        selected: false,
        requires_individual_review: true,
        risk_reason: "hard_pair",
      },
    ],
    selected_count: 0,
    skipped_count: 1,
    automatic_confirmation: false,
  };
}

function annotationResponse(overrides: Record<string, unknown> = {}) {
  return {
    id: "candidate-server",
    image_id: "image-1",
    revision: 1,
    x: 30,
    y: 2,
    width: 20,
    height: 30,
    class_type: "product",
    sku_id: null,
    lifecycle_state: "verified",
    review_state: "accepted",
    source: "human",
    confidence: null,
    occluded: false,
    truncated: false,
    shelf_row: 0,
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
