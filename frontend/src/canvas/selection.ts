import type { ScenePoint } from "./editing";
import type { AnnotationBox } from "./model";

// `primary` is the box the details panel and SKU picker show; `ids` holds every selected
// box, the primary one included.
export type Selection = {
  primary: string | null;
  ids: ReadonlySet<string>;
};

export type SceneRect = {
  x: number;
  y: number;
  width: number;
  height: number;
};

export const EMPTY_SELECTION: Selection = { primary: null, ids: new Set() };

export function selectOnly(id: string | null): Selection {
  return id === null ? EMPTY_SELECTION : { primary: id, ids: new Set([id]) };
}

export function selectMany(ids: readonly string[]): Selection {
  return ids.length === 0 ? EMPTY_SELECTION : { primary: ids[0]!, ids: new Set(ids) };
}

export function addToSelection(selection: Selection, ids: readonly string[]): Selection {
  if (ids.length === 0) {
    return selection;
  }
  return {
    primary: selection.primary ?? ids[0]!,
    ids: new Set([...selection.ids, ...ids]),
  };
}

// Shift-click adds a box, or removes it when it is already selected.
export function toggleSelected(selection: Selection, id: string): Selection {
  const ids = new Set(selection.ids);
  if (!ids.delete(id)) {
    ids.add(id);
    return { primary: id, ids };
  }
  const primary = selection.primary === id ? ([...ids].at(-1) ?? null) : selection.primary;
  return { primary, ids };
}

export function rectBetween(start: ScenePoint, end: ScenePoint): SceneRect {
  return {
    x: Math.min(start.x, end.x),
    y: Math.min(start.y, end.y),
    width: Math.abs(end.x - start.x),
    height: Math.abs(end.y - start.y),
  };
}

// A marquee takes only boxes wholly inside it, so a drag across a dense shelf does not
// pick up the neighbours it grazes.
export function boxesInside(boxes: readonly AnnotationBox[], rect: SceneRect): string[] {
  return boxes
    .filter(
      (box) =>
        box.x >= rect.x &&
        box.y >= rect.y &&
        box.x + box.width <= rect.x + rect.width &&
        box.y + box.height <= rect.y + rect.height,
    )
    .map((box) => box.id);
}
