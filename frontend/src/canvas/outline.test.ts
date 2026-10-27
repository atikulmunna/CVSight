import { describe, expect, it } from "vitest";

import type { PolygonPoint } from "./model";
import { defaultPointCount, outlineWithPoints, rankOutline } from "./outline";

// A rectangle traced with a point halfway along each side.
const TRACED_RECTANGLE: PolygonPoint[] = [
  [100, 120],
  [130, 120],
  [160, 120],
  [160, 165],
  [160, 210],
  [130, 210],
  [100, 210],
  [100, 165],
];

function circle(radius: number, samples: number): PolygonPoint[] {
  return Array.from({ length: samples }, (_, index) => {
    const angle = (2 * Math.PI * index) / samples;
    return [radius + radius * Math.cos(angle), radius + radius * Math.sin(angle)];
  });
}

describe("outline detail", () => {
  it("drops points that carry no shape first and keeps the original order", () => {
    const ranked = rankOutline(TRACED_RECTANGLE);

    expect(outlineWithPoints(ranked, 4)).toEqual([
      [100, 120],
      [160, 120],
      [160, 210],
      [100, 210],
    ]);
    expect(outlineWithPoints(ranked, 8)).toEqual(TRACED_RECTANGLE);
    expect(defaultPointCount(ranked)).toBe(4);
  });

  it("keeps between three points and every point", () => {
    const ranked = rankOutline(TRACED_RECTANGLE);

    expect(outlineWithPoints(ranked, 1)).toHaveLength(3);
    expect(outlineWithPoints(ranked, 50)).toHaveLength(8);
    expect(outlineWithPoints(rankOutline(TRACED_RECTANGLE.slice(0, 3)), 3)).toHaveLength(3);
  });

  it("keeps the same detail for the same shape at any size", () => {
    const points = circle(50, 400);
    const small = rankOutline(points);
    // Scaling by a power of two is exact, so both rankings see the same comparisons.
    const large = rankOutline(points.map(([x, y]): PolygonPoint => [x * 8, y * 8]));

    expect(defaultPointCount(large)).toBe(defaultPointCount(small));
    expect(defaultPointCount(small)).toBeGreaterThanOrEqual(20);
    expect(defaultPointCount(small)).toBeLessThanOrEqual(30);
  });

  it("removes the smaller of two bumps before the larger one", () => {
    const bumps: PolygonPoint[] = [
      [0, 0],
      [40, 0],
      [50, -2],
      [60, 0],
      [100, 0],
      [100, 100],
      [60, 100],
      [50, 115],
      [40, 100],
      [0, 100],
    ];

    const kept = outlineWithPoints(rankOutline(bumps), 9);

    expect(kept).not.toContainEqual([50, -2]);
    expect(kept).toContainEqual([50, 115]);
  });
});
