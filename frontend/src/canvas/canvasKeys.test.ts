import { describe, expect, it } from "vitest";

import { canvasCommand, ZOOM_STEP, type KeyContext, type KeyPress } from "./canvasKeys";

const VERIFY: KeyContext = {
  mode: "verify",
  candidateCount: 0,
  hasUnknownSku: false,
  hasPreviousSku: false,
  selectedIsProduct: true,
  hasSelection: true,
  hasCopy: true,
  hasOutlineDraft: false,
};

const ASSIGN: KeyContext = {
  ...VERIFY,
  mode: "assign",
  candidateCount: 3,
  hasUnknownSku: true,
  hasPreviousSku: true,
};

function press(key: string, modifiers: Partial<KeyPress> = {}): KeyPress {
  return { key, ctrlKey: false, metaKey: false, altKey: false, shiftKey: false, ...modifiers };
}

describe("canvas key commands", () => {
  it("maps Ctrl and Cmd combinations to history, selection, and the clipboard", () => {
    expect(canvasCommand(press("z", { ctrlKey: true }), VERIFY)).toEqual({ type: "undo" });
    expect(canvasCommand(press("Z", { metaKey: true, shiftKey: true }), VERIFY)).toEqual({
      type: "redo",
    });
    expect(canvasCommand(press("y", { ctrlKey: true }), VERIFY)).toEqual({ type: "redo" });
    expect(canvasCommand(press("a", { ctrlKey: true }), VERIFY)).toEqual({ type: "selectAll" });
    expect(canvasCommand(press("c", { ctrlKey: true }), VERIFY)).toEqual({ type: "copy" });
    expect(canvasCommand(press("v", { ctrlKey: true }), VERIFY)).toEqual({ type: "paste" });
  });

  it("leaves Ctrl combinations to the browser when there is nothing to act on", () => {
    expect(canvasCommand(press("r", { ctrlKey: true }), VERIFY)).toBeNull();
    expect(canvasCommand(press("c", { ctrlKey: true }), { ...VERIFY, hasSelection: false })).toBeNull();
    expect(canvasCommand(press("v", { ctrlKey: true }), { ...VERIFY, hasCopy: false })).toBeNull();
    expect(canvasCommand(press("v", { ctrlKey: true }), ASSIGN)).toBeNull();
  });

  it("maps review keys while verifying", () => {
    expect(canvasCommand(press("A"), VERIFY)).toEqual({ type: "accept" });
    for (const key of ["r", "Delete", "Backspace"]) {
      expect(canvasCommand(press(key), VERIFY)).toEqual({ type: "reject" });
    }
    expect(canvasCommand(press("f"), VERIFY)).toEqual({ type: "flag" });
    expect(canvasCommand(press("d"), VERIFY)).toEqual({ type: "duplicate" });
    expect(canvasCommand(press("/"), VERIFY)).toEqual({ type: "changeSku" });
    expect(canvasCommand(press("/"), { ...VERIFY, selectedIsProduct: false })).toBeNull();
  });

  it("nudges with Alt and arrows, resizes with Shift too, and steps without Alt", () => {
    expect(canvasCommand(press("ArrowUp", { altKey: true }), VERIFY)).toEqual({
      type: "editGeometry",
      direction: "up",
      resize: false,
    });
    expect(canvasCommand(press("ArrowLeft", { altKey: true, shiftKey: true }), VERIFY)).toEqual({
      type: "editGeometry",
      direction: "left",
      resize: true,
    });
    expect(canvasCommand(press("ArrowRight"), VERIFY)).toEqual({ type: "step", delta: 1 });
    expect(canvasCommand(press("ArrowRight", { altKey: true }), ASSIGN)).toEqual({
      type: "step",
      delta: 1,
    });
  });

  it("maps assignment keys only to SKUs that exist", () => {
    expect(canvasCommand(press("1"), ASSIGN)).toEqual({ type: "assignCandidate", index: 0 });
    expect(canvasCommand(press("3"), ASSIGN)).toEqual({ type: "assignCandidate", index: 2 });
    expect(canvasCommand(press("4"), ASSIGN)).toBeNull();
    expect(canvasCommand(press("0"), ASSIGN)).toBeNull();
    expect(canvasCommand(press("u"), ASSIGN)).toEqual({ type: "assignUnknown" });
    expect(canvasCommand(press("u"), { ...ASSIGN, hasUnknownSku: false })).toBeNull();
    expect(canvasCommand(press("s"), ASSIGN)).toEqual({ type: "assignPrevious" });
    expect(canvasCommand(press("s"), { ...ASSIGN, hasPreviousSku: false })).toBeNull();
    expect(canvasCommand(press("/"), ASSIGN)).toEqual({ type: "searchCatalog" });
    expect(canvasCommand(press("a"), ASSIGN)).toBeNull();
  });

  it("maps tools, navigation, view, and help keys in both modes", () => {
    for (const context of [VERIFY, ASSIGN]) {
      expect(canvasCommand(press("b"), context)).toEqual({ type: "chooseTool", tool: "product" });
      expect(canvasCommand(press("M"), context)).toEqual({ type: "chooseTool", tool: "smart" });
      expect(canvasCommand(press("k"), context)).toEqual({ type: "step", delta: -1 });
      expect(canvasCommand(press("Escape"), context)).toEqual({ type: "clear" });
      expect(canvasCommand(press("="), context)).toEqual({ type: "zoom", factor: ZOOM_STEP });
      expect(canvasCommand(press("-"), context)).toEqual({ type: "zoom", factor: 1 / ZOOM_STEP });
      expect(canvasCommand(press("?", { shiftKey: true }), context)).toEqual({
        type: "showShortcuts",
      });
      expect(canvasCommand(press("o"), context)).toEqual({ type: "toggleBoxes" });
      expect(canvasCommand(press("x"), context)).toBeNull();
    }
  });

  it("finishes a smart polygon outline with Enter only while one is open", () => {
    expect(canvasCommand(press("Enter"), { ...VERIFY, hasOutlineDraft: true })).toEqual({
      type: "finishOutline",
    });
    expect(canvasCommand(press("Enter"), VERIFY)).toBeNull();
  });
});
