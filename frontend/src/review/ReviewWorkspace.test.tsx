import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { AnnotationBox, CanvasFixture } from "../canvas/model";
import { ReviewApiError, type ReviewQueue, type ReviewSignoff } from "./api";
import { ReviewWorkspace } from "./ReviewWorkspace";

const VERSION_ID = "11111111-1111-4111-8111-111111111111";
const ANNOTATION_ID = "22222222-2222-4222-8222-222222222222";
const IMAGE_ID = "33333333-3333-4333-8333-333333333333";
const SKU_ID = "44444444-4444-4444-8444-444444444444";
const OTHER_ANNOTATION_ID = "55555555-5555-4555-8555-555555555555";
const OTHER_IMAGE_ID = "66666666-6666-4666-8666-666666666666";
const REFERENCE_ID = "77777777-7777-4777-8777-777777777777";
const UNKNOWN_SKU_ID = "00000000-0000-0000-0000-000000000001";

const mocks = vi.hoisted(() => ({
  loadReviewQueue: vi.fn(),
  submitReviewDecision: vi.fn(),
  signOffReview: vi.fn(),
  loadLiveFixture: vi.fn(),
}));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return {
    ...original,
    loadReviewQueue: mocks.loadReviewQueue,
    submitReviewDecision: mocks.submitReviewDecision,
    signOffReview: mocks.signOffReview,
  };
});

vi.mock("../canvas/liveFixture", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../canvas/liveFixture")>()),
  loadLiveFixture: mocks.loadLiveFixture,
}));
vi.mock("../canvas/AnnotationWorkspace", () => ({
  AnnotationWorkspace: () => <div data-testid="review-correction-canvas" />,
}));

describe("ReviewWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.loadLiveFixture.mockResolvedValue(fixture());
    mocks.submitReviewDecision.mockResolvedValue(undefined);
    mocks.signOffReview.mockResolvedValue(signoff());
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("explains how to open a dataset review", () => {
    render(<ReviewWorkspace datasetVersionId={null} />);
    expect(screen.getByText("Open a dataset review")).toBeInTheDocument();
    expect(screen.getByText(/\?version=/)).toBeInTheDocument();
  });

  it("shows a specific missing-version error", async () => {
    mocks.loadReviewQueue.mockRejectedValueOnce(
      new ReviewApiError(404, "dataset_version_not_found"),
    );
    render(<ReviewWorkspace datasetVersionId={VERSION_ID} />);

    expect(
      await screen.findByText("That dataset version does not exist in the local database."),
    ).toBeInTheDocument();
  });

  it("shows every risk, reuses the correction canvas, and signs off a clear queue", async () => {
    mocks.loadReviewQueue
      .mockResolvedValueOnce(openQueue())
      .mockResolvedValueOnce(clearQueue())
      .mockResolvedValueOnce(signedQueue());
    render(<ReviewWorkspace datasetVersionId={VERSION_ID} />);

    expect(await screen.findByText("Fine-grained hard-pair confusion")).toBeInTheDocument();
    expect(screen.getByText("Label originated from propagation")).toBeInTheDocument();
    expect(await screen.findByTestId("review-correction-canvas")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign off snapshot" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "Approve revision" }));
    await waitFor(() =>
      expect(mocks.submitReviewDecision).toHaveBeenCalledWith(
        VERSION_ID,
        expect.objectContaining({ annotationId: ANNOTATION_ID }),
        "approve",
      ),
    );
    expect(await screen.findByText("No blocking review items remain.")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Sign off snapshot" }));
    await waitFor(() => expect(mocks.signOffReview).toHaveBeenCalledWith(VERSION_ID));
    expect(await screen.findByText("Snapshot review is signed.")).toBeInTheDocument();
  });

  it("lines up every accepted box with the SKU next to its reference photo", async () => {
    const fetcher = vi.fn(async () => jsonResponse(lineupPayload()));
    vi.stubGlobal("fetch", fetcher);
    mocks.loadReviewQueue.mockResolvedValueOnce(openQueue());
    mocks.loadLiveFixture.mockResolvedValue(fixture([productBox(SKU_ID)]));
    render(<ReviewWorkspace datasetVersionId={VERSION_ID} />);

    expect(await screen.findByText("Lineup Cola 330 ml")).toBeInTheDocument();
    expect(fetcher).toHaveBeenCalledWith(
      `/api/dataset-versions/${VERSION_ID}/sku-lineup?sku_id=${SKU_ID}`,
      expect.anything(),
    );
    expect(screen.getByAltText("Reference photo cola-front.jpg")).toHaveAttribute(
      "src",
      `/api/sku-reference-images/${REFERENCE_ID}/media/thumbnail`,
    );
    const current = screen.getByAltText("This box on review-shelf.jpg");
    expect(current).toHaveAttribute("src", `/api/annotations/${ANNOTATION_ID}/crop?revision=3`);
    expect(current).toHaveAttribute("loading", "lazy");
    expect(current.closest("figure")).toHaveClass("is-current");
    expect(screen.getByAltText("Box on other-shelf.jpg")).toHaveAttribute(
      "src",
      `/api/annotations/${OTHER_ANNOTATION_ID}/crop?revision=2`,
    );
    expect(screen.queryByAltText("Box on review-shelf.jpg")).not.toBeInTheDocument();
    expect(screen.getByText("Showing the first 2 accepted boxes with this SKU.")).toBeInTheDocument();
  });

  it("says plainly when no other box carries the SKU", async () => {
    const onlyThisBox = { ...lineupPayload(), truncated: false };
    onlyThisBox.items = [onlyThisBox.items[0]!];
    vi.stubGlobal("fetch", vi.fn(async () => jsonResponse(onlyThisBox)));
    mocks.loadReviewQueue.mockResolvedValueOnce(openQueue());
    mocks.loadLiveFixture.mockResolvedValue(fixture([productBox(SKU_ID)]));
    render(<ReviewWorkspace datasetVersionId={VERSION_ID} />);

    expect(
      await screen.findByText("No other accepted box in this version carries this SKU yet."),
    ).toBeInTheDocument();
    expect(screen.getByAltText("This box on review-shelf.jpg")).toBeInTheDocument();
    expect(screen.queryByText(/Showing the first/)).not.toBeInTheDocument();
  });

  it("keeps the current box and says so when the lineup cannot load", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ detail: { code: "review_service_unavailable" } }, 503)),
    );
    mocks.loadReviewQueue.mockResolvedValueOnce(openQueue());
    mocks.loadLiveFixture.mockResolvedValue(fixture([productBox(SKU_ID)]));
    render(<ReviewWorkspace datasetVersionId={VERSION_ID} />);

    expect(
      await screen.findByText("Other boxes with this SKU could not be loaded."),
    ).toBeInTheDocument();
    expect(screen.getByAltText("This box on review-shelf.jpg")).toBeInTheDocument();
    expect(screen.queryByText("Lineup Cola 330 ml")).not.toBeInTheDocument();
  });

  it.each([
    ["an unassigned product", productBox(null)],
    ["the Unknown placeholder SKU", productBox(UNKNOWN_SKU_ID)],
    ["a gap", { ...productBox(null), classType: "gap" as const }],
  ])("shows no lineup for %s", async (_label, box) => {
    const fetcher = vi.fn();
    vi.stubGlobal("fetch", fetcher);
    mocks.loadReviewQueue.mockResolvedValueOnce(openQueue());
    mocks.loadLiveFixture.mockResolvedValue(fixture([box]));
    render(<ReviewWorkspace datasetVersionId={VERSION_ID} />);

    expect(await screen.findByTestId("review-correction-canvas")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "SKU lineup" })).not.toBeInTheDocument();
    expect(fetcher).not.toHaveBeenCalled();
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
      {
        annotation_id: OTHER_ANNOTATION_ID,
        revision: 2,
        image_id: OTHER_IMAGE_ID,
        image_name: "other-shelf.jpg",
        crop_url: `/api/annotations/${OTHER_ANNOTATION_ID}/crop?revision=2`,
      },
    ],
    truncated: true,
  };
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function productBox(skuId: string | null): AnnotationBox {
  return {
    id: ANNOTATION_ID,
    serverId: ANNOTATION_ID,
    imageId: IMAGE_ID,
    revision: 3,
    x: 10,
    y: 10,
    width: 20,
    height: 30,
    polygon: null,
    classType: "product",
    state: "verified",
    lifecycleState: "verified",
    reviewState: "accepted",
    sku: skuId ? "Lineup Cola 330 ml" : "Unknown SKU",
    skuId,
    confidence: null,
    occluded: false,
    truncated: false,
    shelfRow: null,
    imageIndex: 0,
  };
}

function openQueue(): ReviewQueue {
  return {
    datasetVersionId: VERSION_ID,
    status: "open",
    totalRiskItems: 1,
    unresolvedCount: 1,
    resolvedCount: 0,
    signoff: null,
    items: [
      {
        annotationId: ANNOTATION_ID,
        annotationRevision: 3,
        imageId: IMAGE_ID,
        imageName: "review-shelf.jpg",
        imageUrl: `/api/images/${IMAGE_ID}/media/canonical`,
        riskScore: 160,
        resolved: false,
        riskReasons: [
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

function clearQueue(): ReviewQueue {
  return {
    ...openQueue(),
    unresolvedCount: 0,
    resolvedCount: 1,
    items: [],
  };
}

function signedQueue(): ReviewQueue {
  return {
    ...clearQueue(),
    status: "signed",
    signoff: signoff(),
  };
}

function signoff(): ReviewSignoff {
  return {
    datasetVersionId: VERSION_ID,
    signedBy: "reviewer:local",
    signedAt: "2026-08-09T12:00:00Z",
    reviewedAnnotationCount: 1,
    riskItemCount: 1,
  };
}

function fixture(annotations: AnnotationBox[] = []): CanvasFixture {
  return {
    name: "review-shelf.jpg",
    projectType: "object_detection",
    width: 100,
    height: 80,
    images: [
      {
        id: IMAGE_ID,
        url: `/api/images/${IMAGE_ID}/media/canonical`,
        x: 0,
        y: 0,
        width: 100,
        height: 80,
      },
    ],
    annotations,
  };
}
