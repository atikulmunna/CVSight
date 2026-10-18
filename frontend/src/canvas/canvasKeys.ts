import type { CanvasTool } from "./CanvasTools";

export type Direction = "left" | "right" | "up" | "down";

export type CanvasCommand =
  | { type: "undo" }
  | { type: "redo" }
  | { type: "selectAll" }
  | { type: "copy" }
  | { type: "paste" }
  | { type: "assignCandidate"; index: number }
  | { type: "assignUnknown" }
  | { type: "assignPrevious" }
  | { type: "searchCatalog" }
  | { type: "editGeometry"; direction: Direction; resize: boolean }
  | { type: "accept" }
  | { type: "reject" }
  | { type: "changeSku" }
  | { type: "flag" }
  | { type: "duplicate" }
  | { type: "showShortcuts" }
  | { type: "toggleBoxes" }
  | { type: "chooseTool"; tool: CanvasTool }
  | { type: "step"; delta: 1 | -1 }
  | { type: "clear" }
  | { type: "zoom"; factor: number };

export type KeyPress = {
  key: string;
  ctrlKey: boolean;
  metaKey: boolean;
  altKey: boolean;
  shiftKey: boolean;
};

// What the workspace holds right now that decides whether a key has anything to act on.
export type KeyContext = {
  mode: "verify" | "assign";
  candidateCount: number;
  hasUnknownSku: boolean;
  hasPreviousSku: boolean;
  selectedIsProduct: boolean;
  hasSelection: boolean;
  hasCopy: boolean;
};

export const ZOOM_STEP = 1.2;

const TOOL_KEYS: Partial<Record<string, CanvasTool>> = {
  v: "select",
  h: "pan",
  b: "product",
  g: "gap",
};

const ARROWS: Partial<Record<string, Direction>> = {
  arrowleft: "left",
  arrowright: "right",
  arrowup: "up",
  arrowdown: "down",
};

// The command a key press on the canvas asks for, or null to leave the key to the
// browser.
export function canvasCommand(press: KeyPress, context: KeyContext): CanvasCommand | null {
  const key = press.key.toLowerCase();
  if (press.ctrlKey || press.metaKey) {
    return commandKey(key, press.shiftKey, context);
  }
  const modeCommand =
    context.mode === "assign" ? assignKey(key, context) : verifyKey(key, press, context);
  return modeCommand ?? sharedKey(key);
}

// Ctrl and Cmd combinations are canvas commands or the browser's own (Ctrl+R reloads),
// never the single-key shortcuts. Copy and paste with nothing to act on stay with the
// browser.
function commandKey(key: string, shift: boolean, context: KeyContext): CanvasCommand | null {
  if (key === "z") {
    return { type: shift ? "redo" : "undo" };
  }
  if (key === "y") {
    return { type: "redo" };
  }
  if (key === "a") {
    return { type: "selectAll" };
  }
  if (key === "c" && context.hasSelection) {
    return { type: "copy" };
  }
  if (key === "v" && context.mode === "verify" && context.hasCopy) {
    return { type: "paste" };
  }
  return null;
}

function assignKey(key: string, context: KeyContext): CanvasCommand | null {
  const index = Number(key) - 1;
  if (Number.isInteger(index) && index >= 0 && index < context.candidateCount) {
    return { type: "assignCandidate", index };
  }
  if (key === "u" && context.hasUnknownSku) {
    return { type: "assignUnknown" };
  }
  if (key === "s" && context.hasPreviousSku) {
    return { type: "assignPrevious" };
  }
  if (key === "/") {
    return { type: "searchCatalog" };
  }
  return null;
}

function verifyKey(key: string, press: KeyPress, context: KeyContext): CanvasCommand | null {
  const direction = ARROWS[key];
  if (press.altKey && direction) {
    return { type: "editGeometry", direction, resize: press.shiftKey };
  }
  if (key === "a") {
    return { type: "accept" };
  }
  if (key === "r" || key === "delete" || key === "backspace") {
    return { type: "reject" };
  }
  if (key === "/" && context.selectedIsProduct) {
    return { type: "changeSku" };
  }
  if (key === "f") {
    return { type: "flag" };
  }
  if (key === "d") {
    return { type: "duplicate" };
  }
  return null;
}

function sharedKey(key: string): CanvasCommand | null {
  const tool = TOOL_KEYS[key];
  if (key === "?") {
    return { type: "showShortcuts" };
  }
  if (key === "o") {
    return { type: "toggleBoxes" };
  }
  if (tool) {
    return { type: "chooseTool", tool };
  }
  if (key === "arrowright" || key === "j") {
    return { type: "step", delta: 1 };
  }
  if (key === "arrowleft" || key === "k") {
    return { type: "step", delta: -1 };
  }
  if (key === "escape") {
    return { type: "clear" };
  }
  if (key === "+" || key === "=") {
    return { type: "zoom", factor: ZOOM_STEP };
  }
  if (key === "-") {
    return { type: "zoom", factor: 1 / ZOOM_STEP };
  }
  return null;
}
