import { describe, expect, it } from "vitest";

import { acceptBox, flagBox, rejectBox } from "./editing";
import {
  EMPTY_HISTORY,
  mergeChange,
  recordAction,
  takeRedo,
  takeUndo,
  transitionSteps,
  type BoxChange,
} from "./history";
import type { AnnotationBox } from "./model";

const BOX: AnnotationBox = {
  id: "box-1",
  serverId: "server-1",
  imageId: "image-1",
  revision: 3,
  x: 10,
  y: 10,
  width: 20,
  height: 30,
  classType: "product",
  state: "unverified",
  lifecycleState: "proposed",
  reviewState: "unreviewed",
  sku: "Unknown SKU",
  skuId: null,
  confidence: 0.7,
  occluded: false,
  truncated: false,
  shelfRow: 0,
  imageIndex: 0,
};

const ASSIGNED: AnnotationBox = {
  ...acceptBox(BOX),
  sku: "Aurora Cola",
  skuId: "sku-1",
};

function change(before: AnnotationBox | null, after: AnnotationBox): BoxChange {
  return { id: after.id, before, after };
}

describe("edit history", () => {
  it("records actions, ignores empty ones, and clears redo on a new action", () => {
    const first = recordAction(EMPTY_HISTORY, [change(BOX, flagBox(BOX))]);
    const undone = takeUndo(first)!.history;

    expect(recordAction(first, [])).toBe(first);
    expect(undone.redo).toHaveLength(1);
    expect(recordAction(undone, [change(BOX, acceptBox(BOX))])).toMatchObject({
      undo: [[{ after: { reviewState: "accepted" } }]],
      redo: [],
    });
  });

  it("keeps only the most recent 200 actions", () => {
    let history = EMPTY_HISTORY;
    for (let index = 0; index < 205; index += 1) {
      history = recordAction(history, [change(BOX, { ...BOX, x: index })]);
    }

    expect(history.undo).toHaveLength(200);
    expect(history.undo[0]![0]!.after.x).toBe(5);
  });

  it("moves an action between the undo and redo stacks", () => {
    const action = [change(BOX, flagBox(BOX))];
    const recorded = recordAction(EMPTY_HISTORY, action);

    const undone = takeUndo(recorded)!;
    const redone = takeRedo(undone.history)!;

    expect(undone.changes).toBe(action);
    expect(undone.history).toEqual({ undo: [], redo: [action] });
    expect(redone.changes).toBe(action);
    expect(redone.history).toEqual({ undo: [action], redo: [] });
    expect(takeUndo(EMPTY_HISTORY)).toBeNull();
    expect(takeRedo(recorded)).toBeNull();
  });

  it("collapses several edits to one box into one change", () => {
    const accepted = acceptBox(BOX);
    const other = { ...BOX, id: "box-2" };

    const merged = mergeChange(
      mergeChange([change(BOX, accepted)], change(other, flagBox(other))),
      change(accepted, ASSIGNED),
    );

    expect(merged).toEqual([change(BOX, ASSIGNED), change(other, flagBox(other))]);
  });
});

describe("transition steps", () => {
  it("rejects a box the undone action created, once", () => {
    expect(transitionSteps(BOX, null)).toEqual([
      { intent: "reject", box: rejectBox(BOX) },
    ]);
    expect(transitionSteps(rejectBox(BOX), null)).toEqual([]);
  });

  it("rejects again when redoing a rejection", () => {
    expect(transitionSteps(BOX, rejectBox(BOX))).toEqual([
      { intent: "reject", box: rejectBox(BOX) },
    ]);
  });

  it("restores a rejected box with the server's current revision", () => {
    const rejected = { ...rejectBox(ASSIGNED), revision: 5 };

    expect(transitionSteps(rejected, ASSIGNED)).toEqual([
      { intent: "restore", box: { ...ASSIGNED, revision: 5 } },
    ]);
  });

  it("reopens an acceptance and puts back the review state from before it", () => {
    const flagged = flagBox(BOX);
    const accepted = { ...acceptBox(flagged), revision: 4 };

    expect(transitionSteps({ ...acceptBox(BOX), revision: 4 }, BOX)).toEqual([
      {
        intent: "reopen",
        box: { ...BOX, revision: 4 },
      },
    ]);
    expect(transitionSteps(accepted, flagged).map((step) => step.intent)).toEqual([
      "reopen",
      "update",
    ]);
    expect(transitionSteps(accepted, flagged).at(-1)!.box).toEqual({
      ...flagged,
      revision: 4,
    });
  });

  it("accepts and then updates the SKU when redoing an assignment", () => {
    const steps = transitionSteps(BOX, ASSIGNED);

    expect(steps.map((step) => step.intent)).toEqual(["accept", "update"]);
    expect(steps[1]!.box).toEqual(ASSIGNED);
  });

  it("updates edited fields in place and does nothing when nothing differs", () => {
    const moved = { ...BOX, x: 14, confidence: null, revision: 1 };

    expect(transitionSteps(moved, BOX)).toEqual([
      { intent: "update", box: { ...BOX, revision: 1 } },
    ]);
    expect(transitionSteps(BOX, { ...BOX, state: "propagated" })).toEqual([]);
  });
});
