import { acceptBox, rejectBox, reopenBox, type EditIntent } from "./editing";
import type { AnnotationBox } from "./model";

// One box's part in a user action. `before` is null when the action created the box.
export type BoxChange = {
  id: string;
  before: AnnotationBox | null;
  after: AnnotationBox;
};

export type EditHistory = {
  undo: BoxChange[][];
  redo: BoxChange[][];
};

export type EditStep = {
  intent: EditIntent;
  box: AnnotationBox;
};

export const EMPTY_HISTORY: EditHistory = { undo: [], redo: [] };

const HISTORY_LIMIT = 200;

// The fields a PUT replaces; identity and the derived overlay state are not compared.
const EDITABLE_FIELDS = [
  "x",
  "y",
  "width",
  "height",
  "classType",
  "skuId",
  "reviewState",
  "confidence",
  "occluded",
  "truncated",
  "shelfRow",
] as const;

export function recordAction(history: EditHistory, changes: BoxChange[]): EditHistory {
  if (changes.length === 0) {
    return history;
  }
  return { undo: [...history.undo, changes].slice(-HISTORY_LIMIT), redo: [] };
}

// Several edits to one box inside one action collapse into a single change from its
// first state to its last, so accept then assign undoes in one step.
export function mergeChange(changes: BoxChange[], change: BoxChange): BoxChange[] {
  if (!changes.some((existing) => existing.id === change.id)) {
    return [...changes, change];
  }
  return changes.map((existing) =>
    existing.id === change.id ? { ...existing, after: change.after } : existing,
  );
}

export function takeUndo(
  history: EditHistory,
): { history: EditHistory; changes: BoxChange[] } | null {
  const changes = history.undo.at(-1);
  if (!changes) {
    return null;
  }
  return {
    history: { undo: history.undo.slice(0, -1), redo: [...history.redo, changes] },
    changes,
  };
}

export function takeRedo(
  history: EditHistory,
): { history: EditHistory; changes: BoxChange[] } | null {
  const changes = history.redo.at(-1);
  if (!changes) {
    return null;
  }
  return {
    history: { undo: [...history.undo, changes], redo: history.redo.slice(0, -1) },
    changes,
  };
}

// The server intents that move a box from its current state to an earlier or later one.
// Each step appends a revision, so undo never rewrites the server's history and a stale
// revision still surfaces as a conflict.
export function transitionSteps(
  current: AnnotationBox,
  target: AnnotationBox | null,
): EditStep[] {
  if (target === null || target.lifecycleState === "rejected") {
    return current.lifecycleState === "rejected"
      ? []
      : [{ intent: "reject", box: rejectBox(current) }];
  }
  const steps: EditStep[] = [];
  let box = current;
  if (box.lifecycleState === "rejected") {
    // Restore returns the revision saved before the rejection. Edits save in order, so
    // that revision is the target.
    box = withIdentity(target, box);
    steps.push({ intent: "restore", box });
  } else if (box.lifecycleState === "proposed" && target.lifecycleState === "verified") {
    box = acceptBox(box);
    steps.push({ intent: "accept", box });
  } else if (box.lifecycleState === "verified" && target.lifecycleState === "proposed") {
    box = reopenBox(box);
    steps.push({ intent: "reopen", box });
  }
  if (!sameEditableFields(box, target)) {
    steps.push({ intent: "update", box: withIdentity(target, box) });
  }
  return steps;
}

function sameEditableFields(left: AnnotationBox, right: AnnotationBox): boolean {
  return EDITABLE_FIELDS.every((field) => left[field] === right[field]);
}

// A historical copy carries the revision it had then; the server expects the current one.
function withIdentity(target: AnnotationBox, current: AnnotationBox): AnnotationBox {
  return {
    ...target,
    serverId: current.serverId,
    imageId: current.imageId,
    revision: current.revision,
  };
}
