import type { AnnotationBox, FixtureImage } from "./model";

export type CopiedBoxes = {
  boxes: AnnotationBox[];
  // The photos the boxes came from, so a paste can keep each box's place in its photo.
  images: FixtureImage[];
};

// The workspace remounts for every photo, so a copy is kept here to paste into the next
// one. It lasts until the page reloads.
let copied: CopiedBoxes | null = null;

export function copyBoxes(value: CopiedBoxes): void {
  copied = value;
}

export function copiedBoxes(): CopiedBoxes | null {
  return copied;
}
