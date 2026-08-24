import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { AnalyticsApiError, type AnalyticsReport } from "./api";
import { AnalyticsWorkspace } from "./AnalyticsWorkspace";

const VERSION_ID = "11111111-1111-4111-8111-111111111111";
const mocks = vi.hoisted(() => ({ loadAnalytics: vi.fn() }));

vi.mock("./api", async (importOriginal) => {
  const original = await importOriginal<typeof import("./api")>();
  return { ...original, loadAnalytics: mocks.loadAnalytics };
});

describe("AnalyticsWorkspace", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("explains how to open snapshot analytics", () => {
    render(<AnalyticsWorkspace datasetVersionId={null} />);
    expect(screen.getByText("Open an immutable snapshot")).toBeInTheDocument();
  });

  it("shows lineage, final shares, exports, and unsupported planogram status", async () => {
    mocks.loadAnalytics.mockResolvedValue(report());
    render(<AnalyticsWorkspace datasetVersionId={VERSION_ID} />);

    expect(await screen.findByText("Retail analytics")).toBeInTheDocument();
    expect(screen.getAllByText("50%")).toHaveLength(2);
    expect(screen.getByText("Planogram comparison unavailable")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Export CSV" })).toHaveAttribute(
      "href",
      `/api/dataset-versions/${VERSION_ID}/analytics/export?format=csv`,
    );
    expect(screen.getByText("Final")).toBeInTheDocument();
  });

  it("never presents incomplete share as final", async () => {
    const value = report();
    value.images[0]!.countShare = {
      status: "partial",
      isFinal: false,
      reason: "incomplete_observation",
      shares: { "sku-a": 0.5, unknown: 0.5 },
    };
    mocks.loadAnalytics.mockResolvedValue(value);
    render(<AnalyticsWorkspace datasetVersionId={VERSION_ID} />);

    expect(await screen.findByText("Not final")).toBeInTheDocument();
    expect(screen.getByText(/is partial and is not a final fact/)).toBeInTheDocument();
    expect(screen.queryByText("50%")).not.toBeInTheDocument();
  });

  it("explains that an open version must be frozen", async () => {
    mocks.loadAnalytics.mockRejectedValue(
      new AnalyticsApiError(409, "dataset_version_not_frozen"),
    );
    render(<AnalyticsWorkspace datasetVersionId={VERSION_ID} />);

    expect(
      await screen.findByText("Freeze the dataset version before generating analytics."),
    ).toBeInTheDocument();
  });
});

function report(): AnalyticsReport {
  return {
    datasetVersionId: VERSION_ID,
    formulaVersion: "shelfsight-retail-analytics/v1",
    resultSha256: "b".repeat(64),
    generatedAt: "2026-08-24T13:00:00+00:00",
    snapshotSha256: "a".repeat(64),
    groupLabels: { "sku-a": "Cola", unknown: "Unknown" },
    summary: {
      images: 1,
      reviewedImages: 1,
      acceptedProductFacings: 2,
      acceptedGaps: 1,
      finalCountShareImages: 1,
    },
    planogramStatus: "unsupported",
    planogramReason: "no_planogram_reference",
    images: [
      {
        imageId: "33333333-3333-4333-8333-333333333333",
        imageName: "shelf.jpg",
        imageStatus: "reviewed",
        activeProductFacings: 2,
        acceptedProductFacings: 2,
        countShare: {
          status: "complete",
          isFinal: true,
          reason: null,
          shares: { "sku-a": 0.5, unknown: 0.5 },
        },
        imageAreaShare: {
          status: "complete",
          isFinal: true,
          reason: null,
          shares: { "sku-a": 0.5, unknown: 0.5 },
        },
        realogramStatus: "complete",
        acceptedGaps: 1,
      },
    ],
  };
}
