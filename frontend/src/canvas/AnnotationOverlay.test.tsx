import { render } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnnotationOverlay } from "./AnnotationOverlay";
import type { AnnotationBox } from "./model";

const BOX: AnnotationBox = {
  id: "box-1",
  serverId: null,
  imageId: null,
  revision: null,
  x: 10,
  y: 10,
  width: 20,
  height: 30,
  polygon: null,
  classType: "product",
  state: "unverified",
  lifecycleState: "proposed",
  reviewState: "unreviewed",
  sku: "Test SKU",
  skuId: null,
  confidence: null,
  occluded: false,
  truncated: false,
  shelfRow: 0,
  imageIndex: 0,
};

function overlay(box: AnnotationBox, selectedId: string | null = null) {
  return render(
    <AnnotationOverlay
      annotations={[box]}
      width={100}
      height={80}
      selectedId={selectedId}
      interactive
      onSelect={vi.fn()}
    />,
  ).container;
}

describe("AnnotationOverlay", () => {
  it("draws a product outline in place of its box", () => {
    const container = overlay({ ...BOX, polygon: [[10, 10], [30, 25], [20, 40]] });

    expect(container.querySelector(".overlay-core")?.tagName).toBe("polygon");
    expect(container.querySelector(".overlay-core")?.getAttribute("points")).toBe(
      "10,10 30,25 20,40",
    );
  });

  it("gives a selected outline a handle on every point and edge instead of box handles", () => {
    const container = overlay({ ...BOX, polygon: [[10, 10], [30, 25], [20, 40]] }, BOX.id);
    const handles = [...container.querySelectorAll("[data-handle]")].map((handle) =>
      handle.getAttribute("data-handle"),
    );

    expect(handles).toEqual([
      "midpoint-0",
      "midpoint-1",
      "midpoint-2",
      "vertex-0",
      "vertex-1",
      "vertex-2",
    ]);
    expect(container.querySelector(".selection-outline")?.tagName).toBe("polygon");
  });

  it("draws the box when there is no outline", () => {
    const container = overlay(BOX);

    expect(container.querySelector(".overlay-core")?.tagName).toBe("rect");
  });
});
