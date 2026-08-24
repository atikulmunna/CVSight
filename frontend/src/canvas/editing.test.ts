import { describe, expect, it } from "vitest";

import {
  acceptBox,
  assignSku,
  changeBoxClass,
  createGapBox,
  duplicateBox,
  flagBox,
  nudgeBox,
  rejectBox,
  resizeBox,
} from "./editing";
import type { AnnotationBox } from "./model";

const BOX: AnnotationBox = {
  id: "box-1",
  serverId: "server-1",
  imageId: "image-1",
  revision: 1,
  x: 10,
  y: 10,
  width: 20,
  height: 30,
  classType: "product",
  state: "unverified",
  lifecycleState: "proposed",
  reviewState: "unreviewed",
  sku: "Test SKU",
  skuId: null,
  confidence: 0.8,
  occluded: false,
  truncated: false,
  shelfRow: 0,
  imageIndex: 0,
};

const BOUNDS = { width: 100, height: 80 };

describe("annotation editing", () => {
  it("creates a bounded unreviewed gap from either drag direction", () => {
    const gap = createGapBox(
      "new-gap",
      "image-1",
      0,
      { x: 90, y: 80 },
      { x: 20, y: 30 },
      { width: 100, height: 100 },
      2,
    );

    expect(gap).toMatchObject({
      id: "new-gap",
      imageId: "image-1",
      x: 20,
      y: 30,
      width: 70,
      height: 50,
      classType: "gap",
      shelfRow: 2,
      lifecycleState: "proposed",
      reviewState: "unreviewed",
    });
  });

  it("ignores accidental gap clicks smaller than the minimum box size", () => {
    expect(
      createGapBox(
        "new-gap",
        "image-1",
        0,
        { x: 10, y: 10 },
        { x: 12, y: 12 },
        { width: 100, height: 100 },
        0,
      ),
    ).toBeNull();
  });

  it("clears stale confidence when a human changes geometry", () => {
    expect(nudgeBox(BOX, 1, 0, BOUNDS).confidence).toBeNull();
    expect(resizeBox(BOX, 1, 0, BOUNDS).confidence).toBeNull();
  });
  it("applies accept, flag, and reject lifecycle states", () => {
    expect(acceptBox(BOX)).toMatchObject({
      state: "verified",
      lifecycleState: "verified",
      reviewState: "accepted",
    });
    expect(flagBox(BOX)).toMatchObject({
      state: "flagged",
      lifecycleState: "proposed",
      reviewState: "flagged",
    });
    expect(rejectBox(BOX).lifecycleState).toBe("rejected");
  });

  it("does not edit rejected annotations", () => {
    const rejected = rejectBox(BOX);

    expect(acceptBox(rejected)).toBe(rejected);
    expect(flagBox(rejected)).toBe(rejected);
  });

  it("does not create redundant accepted or flagged edits", () => {
    const accepted = acceptBox(BOX);
    const flagged = flagBox(BOX);

    expect(acceptBox(accepted)).toBe(accepted);
    expect(flagBox(flagged)).toBe(flagged);
  });

  it("assigns only accepted product boxes", () => {
    const accepted = acceptBox(BOX);

    expect(assignSku(accepted, "sku-2", "Second SKU")).toMatchObject({
      skuId: "sku-2",
      sku: "Second SKU",
    });
    expect(assignSku(BOX, "sku-2", "Second SKU")).toBe(BOX);
    expect(
      assignSku(
        { ...accepted, classType: "gap" },
        "sku-2",
        "Second SKU",
      ),
    ).toMatchObject({ skuId: null });
  });

  it("changes class without retaining SKU identity or accepted review", () => {
    const accepted = {
      ...acceptBox(BOX),
      skuId: "sku-1",
      sku: "Test SKU",
    };

    expect(changeBoxClass(accepted, "gap")).toMatchObject({
      classType: "gap",
      skuId: null,
      sku: "Visible gap",
      confidence: null,
      state: "unverified",
      reviewState: "unreviewed",
    });
    expect(changeBoxClass(accepted, "product")).toBe(accepted);
    expect(changeBoxClass(rejectBox(accepted), "gap")).toMatchObject({
      classType: "product",
    });
  });

  it("clamps nudges and resizes to the image bounds and minimum size", () => {
    expect(nudgeBox(BOX, -50, 100, BOUNDS)).toMatchObject({ x: 0, y: 50 });
    expect(resizeBox(BOX, -100, 100, BOUNDS)).toMatchObject({
      width: 4,
      height: 70,
    });
  });

  it("duplicates as a new unverified proposal inside the image", () => {
    expect(
      duplicateBox({ ...BOX, x: 80, y: 50 }, "draft-2", BOUNDS),
    ).toMatchObject({
      id: "draft-2",
      serverId: null,
      revision: null,
      x: 80,
      y: 50,
      state: "unverified",
      lifecycleState: "proposed",
      reviewState: "unreviewed",
      confidence: null,
    });
  });
});
