import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { GapReviewNavigator } from "./GapReviewNavigator";
import type { GapReviewManifest } from "./gapReview";

const MANIFEST: GapReviewManifest = {
  datasetVersionId: "00000000-0000-4000-8000-000000000001",
  reviewMode: "candidate-review",
  items: [
    {
      position: 1,
      imageId: "00000000-0000-4000-8000-000000000002",
      sourceImageId: "source-1",
      name: "first.jpg",
      split: "validation",
      captureGroup: "capture-1",
      candidateCount: 2,
    },
    {
      position: 2,
      imageId: "00000000-0000-4000-8000-000000000003",
      sourceImageId: "source-2",
      name: "second.jpg",
      split: "test",
      captureGroup: "capture-2",
      candidateCount: 0,
    },
  ],
};

describe("GapReviewNavigator", () => {
  it("explains unresolved decisions and blocks completion and forward navigation", () => {
    renderNavigator({ unresolvedCount: 2, saveStatus: "saved" });

    expect(screen.getByRole("button", { name: "2 decisions remaining" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Next image" })).toBeDisabled();
  });

  it("waits for autosave before enabling image completion", () => {
    const { rerender } = renderNavigator({ unresolvedCount: 0, saveStatus: "saving" });
    expect(screen.getByRole("button", { name: "Saving changes" })).toBeDisabled();

    rerender(navigator({ unresolvedCount: 0, saveStatus: "saved" }));
    expect(screen.getByRole("button", { name: "Mark reviewed" })).toBeEnabled();
  });

  it("allows forward navigation only after the image is reviewed", () => {
    const onSelect = vi.fn();
    renderNavigator({ reviewed: true, onSelect });

    fireEvent.click(screen.getByRole("button", { name: "Next image" }));
    expect(onSelect).toHaveBeenCalledWith(1);
  });

  it("keeps split membership hidden during blind labeling", () => {
    renderNavigator({
      manifest: { ...MANIFEST, reviewMode: "blind-truth" },
    });

    expect(screen.getByText("BLIND GROUND TRUTH")).toBeInTheDocument();
    expect(screen.getByText("Image 1 of 2")).toBeInTheDocument();
    expect(screen.queryByText(/validation image/i)).not.toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Confirm image reviewed" }),
    ).toBeEnabled();
  });
});

function renderNavigator(overrides: Partial<Parameters<typeof GapReviewNavigator>[0]> = {}) {
  const view = render(navigator(overrides));
  return view;
}

function navigator(overrides: Partial<Parameters<typeof GapReviewNavigator>[0]> = {}) {
  return (
    <GapReviewNavigator
      manifest={MANIFEST}
      index={0}
      busy={false}
      reviewed={false}
      unresolvedCount={0}
      saveStatus="saved"
      error={null}
      onSelect={() => undefined}
      onMarkReviewed={() => undefined}
      {...overrides}
    />
  );
}
