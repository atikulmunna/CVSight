import { describe, expect, it } from "vitest";

import { fitTransform, revealBox, zoomTransform } from "./transform";

describe("canvas transforms", () => {
  it("fits and centers a scene", () => {
    expect(
      fitTransform({ width: 1_000, height: 500 }, { width: 2_000, height: 500 }),
    ).toEqual({ scale: 0.5, x: 0, y: 125 });
  });

  it("keeps the zoom anchor fixed and clamps scale", () => {
    expect(zoomTransform({ scale: 1, x: 0, y: 0 }, 2, { x: 100, y: 50 })).toEqual({
      scale: 2,
      x: -100,
      y: -50,
    });
    expect(
      zoomTransform({ scale: 1, x: 0, y: 0 }, 100, { x: 0, y: 0 }).scale,
    ).toBe(4);
  });

  it("pans only enough to reveal the selected box", () => {
    const next = revealBox(
      { scale: 1, x: 0, y: 0 },
      { width: 300, height: 200 },
      {
        id: "box",
        serverId: null,
        imageId: null,
        revision: null,
        x: 280,
        y: 170,
        width: 30,
        height: 40,
        classType: "product",
        state: "verified",
        lifecycleState: "verified",
        reviewState: "accepted",
        sku: "SKU",
        skuId: null,
        confidence: null,
        occluded: false,
        truncated: false,
        shelfRow: 0,
        imageIndex: 0,
      },
      20,
    );

    expect(next).toEqual({ scale: 1, x: -30, y: -30 });
  });
});
