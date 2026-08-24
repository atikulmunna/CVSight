import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { AnnotationBox, CanvasFixture } from "./model";
import { PropagationWorkspace } from "./PropagationWorkspace";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("PropagationWorkspace", () => {
  it("shows truthful counts and requires a crop click before confirmation", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response(201, suggestionResponse()))
      .mockResolvedValueOnce(
        response(200, {
          suggestion_set_id: "set-1",
          selected_count: 1,
          skipped_count: 0,
          annotations: [annotationResponse()],
        }),
      );
    vi.stubGlobal("fetch", fetcher);
    const onAnnotationsChange = vi.fn();
    render(
      <PropagationWorkspace
        fixture={FIXTURE}
        annotations={FIXTURE.annotations}
        onAnnotationsChange={onAnnotationsChange}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "Find similar crops" }));
    await screen.findByText("Fine-grained confusion risk");

    expect(document.querySelector(".propagation-confirm-bar")).toHaveTextContent(
      "0 selected · 1 skipped",
    );
    const candidate = screen.getByRole("button", { name: /93%Skipped/ });
    expect(candidate).toHaveAttribute("aria-pressed", "false");

    fireEvent.click(candidate);
    expect(document.querySelector(".propagation-confirm-bar")).toHaveTextContent(
      "1 selected · 0 skipped",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Confirm 1 · skip 0" }),
    );

    await waitFor(() => expect(onAnnotationsChange).toHaveBeenCalledOnce());
    expect(onAnnotationsChange.mock.calls[0]![0][1]).toMatchObject({
      id: "candidate-local",
      revision: 2,
      skuId: "sku-1",
      sku: "Seed SKU",
    });
    const confirmInit = fetcher.mock.calls[1]![1] as RequestInit;
    expect(JSON.parse(confirmInit.body as string).decisions[0]).toEqual({
      suggestion_id: "suggestion-1",
      decision: "confirm",
      reviewed_individually: true,
    });
  });
});

const SEED: AnnotationBox = {
  id: "seed-local",
  serverId: "seed-server",
  imageId: "image-1",
  revision: 3,
  x: 5,
  y: 5,
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

const FIXTURE: CanvasFixture = {
  name: "Propagation fixture",
  width: 100,
  height: 80,
  images: [
    {
      id: "image-1",
      url: "/fixture.jpg",
      x: 0,
      y: 0,
      width: 100,
      height: 80,
    },
  ],
  annotations: [SEED, CANDIDATE],
};

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
        y: 5,
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

function annotationResponse() {
  return {
    id: "candidate-server",
    image_id: "image-1",
    revision: 2,
    x: 30,
    y: 5,
    width: 20,
    height: 30,
    class_type: "product",
    sku_id: "sku-1",
    lifecycle_state: "verified",
    review_state: "accepted",
    source: "propagated",
    confidence: null,
    occluded: false,
    truncated: false,
    shelf_row: 0,
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}
