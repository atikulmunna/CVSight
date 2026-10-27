import { describe, expect, it } from "vitest";

import type { AnnotationBox } from "./model";
import {
  EMPTY_SELECTION,
  addToSelection,
  boxesInside,
  rectBetween,
  selectMany,
  selectOnly,
  toggleSelected,
} from "./selection";

function box(id: string, x: number, y: number, width = 10, height = 10): AnnotationBox {
  return {
    id,
    serverId: null,
    imageId: null,
    revision: null,
    x,
    y,
    width,
    height,
    polygon: null,
    classType: "product",
    state: "unverified",
    lifecycleState: "proposed",
    reviewState: "unreviewed",
    sku: "Unknown SKU",
    skuId: null,
    confidence: null,
    occluded: false,
    truncated: false,
    shelfRow: 0,
    imageIndex: 0,
  };
}

describe("selection", () => {
  it("selects one box or nothing", () => {
    expect(selectOnly("a")).toEqual({ primary: "a", ids: new Set(["a"]) });
    expect(selectOnly(null)).toBe(EMPTY_SELECTION);
  });

  it("makes the first of many the primary box", () => {
    expect(selectMany(["b", "a"])).toEqual({ primary: "b", ids: new Set(["b", "a"]) });
    expect(selectMany([])).toBe(EMPTY_SELECTION);
  });

  it("adds boxes and keeps the primary one", () => {
    const selection = addToSelection(selectOnly("a"), ["b", "a"]);

    expect(selection).toEqual({ primary: "a", ids: new Set(["a", "b"]) });
    expect(addToSelection(EMPTY_SELECTION, ["c"]).primary).toBe("c");
    expect(addToSelection(selection, [])).toBe(selection);
  });

  it("toggles a box in and out and moves the primary role when it leaves", () => {
    const added = toggleSelected(selectOnly("a"), "b");
    expect(added).toEqual({ primary: "b", ids: new Set(["a", "b"]) });

    expect(toggleSelected(added, "b")).toEqual({ primary: "a", ids: new Set(["a"]) });
    expect(toggleSelected(added, "a")).toEqual({ primary: "b", ids: new Set(["b"]) });
    expect(toggleSelected(selectOnly("a"), "a")).toEqual({ primary: null, ids: new Set() });
  });

  it("normalizes a marquee dragged in any direction", () => {
    expect(rectBetween({ x: 30, y: 5 }, { x: 10, y: 25 })).toEqual({
      x: 10,
      y: 5,
      width: 20,
      height: 20,
    });
  });

  it("takes only boxes wholly inside the marquee", () => {
    const boxes = [box("inside", 10, 10), box("edge", 20, 20), box("grazed", 25, 10)];

    expect(boxesInside(boxes, { x: 10, y: 10, width: 20, height: 20 })).toEqual([
      "inside",
      "edge",
    ]);
  });
});
