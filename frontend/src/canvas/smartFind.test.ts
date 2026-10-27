import { describe, expect, it } from "vitest";

import type { AnnotationBox, FixtureImage, PolygonPoint } from "./model";
import { foundProposals, overlap } from "./smartFind";

// The second photo of a tiled scene, so finds must move by its offset.
const IMAGE: FixtureImage = { id: "image-2", url: "", x: 100, y: 0, width: 100, height: 80 };
const LABELED: AnnotationBox = {
  id: "labeled",
  serverId: null,
  imageId: "image-2",
  revision: null,
  x: 110,
  y: 10,
  width: 20,
  height: 30,
  polygon: null,
  classType: "product",
  state: "verified",
  lifecycleState: "verified",
  reviewState: "accepted",
  sku: "Test SKU",
  skuId: null,
  confidence: null,
  occluded: false,
  truncated: false,
  shelfRow: 0,
  imageIndex: 1,
};

function square(x: number, y: number, size: number): PolygonPoint[] {
  return [
    [x, y],
    [x + size / 2, y],
    [x + size, y],
    [x + size, y + size],
    [x, y + size],
  ];
}

function propose(found: Array<{ polygon: PolygonPoint[]; score: number }>, keepOutlines = true) {
  let count = 0;
  return foundProposals(found, {
    image: IMAGE,
    imageIndex: 1,
    bounds: { width: 200, height: 80 },
    labeled: [LABELED],
    keepOutlines,
    newId: () => `found-${++count}`,
    shelfRowAt: (y) => (y < 40 ? 0 : 1),
  });
}

describe("found proposals", () => {
  it("turns SAM's finds into thinned, unreviewed product proposals on their photo", () => {
    const [proposal] = propose([{ polygon: square(50, 45, 20), score: 0.8 }]);

    expect(proposal).toMatchObject({
      id: "found-1",
      imageId: "image-2",
      imageIndex: 1,
      x: 150,
      y: 45,
      width: 20,
      height: 20,
      classType: "product",
      lifecycleState: "proposed",
      reviewState: "unreviewed",
      confidence: 0.8,
      shelfRow: 1,
    });
    // The point halfway along the top edge carries no shape, so it is thinned away.
    expect(proposal!.polygon).toEqual([
      [150, 45],
      [170, 45],
      [170, 65],
      [150, 65],
    ]);
  });

  it("leaves out finds that repeat a labeled box or an earlier find", () => {
    const proposals = propose([
      { polygon: square(10, 11, 22), score: 0.9 },
      { polygon: square(50, 45, 20), score: 0.8 },
      { polygon: square(51, 45, 20), score: 0.7 },
    ]);

    expect(proposals.map((box) => box.x)).toEqual([150]);
  });

  it("keeps only the box in detection projects", () => {
    const [proposal] = propose([{ polygon: square(50, 45, 20), score: 0.8 }], false);

    expect(proposal!.polygon).toBeNull();
    expect(proposal!.width).toBe(20);
  });

  it("measures overlap as intersection over union", () => {
    const box = { x: 0, y: 0, width: 10, height: 10 };

    expect(overlap(box, box)).toBe(1);
    expect(overlap(box, { x: 5, y: 0, width: 10, height: 10 })).toBeCloseTo(1 / 3);
    expect(overlap(box, { x: 20, y: 0, width: 10, height: 10 })).toBe(0);
  });
});
