import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CanvasFixture } from "../canvas/model";
import { ReviewApiError, type ReviewQueue, type ReviewSignoff } from "./api";
import { ReviewWorkspace } from "./ReviewWorkspace";

const VERSION_ID = "11111111-1111-4111-8111-111111111111";
const ANNOTATION_ID = "22222222-2222-4222-8222-222222222222";
const IMAGE_ID = "33333333-3333-4333-8333-333333333333";

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

vi.mock("../canvas/liveFixture", () => ({
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
});

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

function fixture(): CanvasFixture {
  return {
    name: "review-shelf.jpg",
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
    annotations: [],
  };
}
