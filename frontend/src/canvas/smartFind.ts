import { createOutlinedBox, type BoxGeometry, type SceneBounds } from "./editing";
import type { AnnotationBox, FixtureImage, PolygonPoint } from "./model";
import { defaultPointCount, outlineWithPoints, rankOutline } from "./outline";

// A find that overlaps a labeled box at least this much is that box found again.
const SAME_OBJECT_IOU = 0.5;

export type FoundObject = {
  // In the photo's own pixels.
  polygon: PolygonPoint[];
  score: number;
};

type ProposalContext = {
  image: FixtureImage;
  imageIndex: number;
  bounds: SceneBounds;
  // Every box already on the canvas; a find that repeats one is left out.
  labeled: readonly AnnotationBox[];
  // Instance segmentation projects keep the outline; detection projects keep the box.
  keepOutlines: boolean;
  newId: () => string;
  shelfRowAt: (y: number) => number;
};

// SAM's finds as new product proposals on the canvas. Each outline is thinned the way a
// smart polygon opens, the box is its bounds, and SAM's score becomes the confidence. A
// find that repeats a labeled box, or an earlier find, is left out.
export function foundProposals(
  found: readonly FoundObject[],
  context: ProposalContext,
): AnnotationBox[] {
  const { image, imageIndex } = context;
  const proposals: AnnotationBox[] = [];
  for (const { polygon, score } of found) {
    const ranked = rankOutline(polygon.map(([x, y]) => [image.x + x, image.y + y]));
    const outline = outlineWithPoints(ranked, defaultPointCount(ranked));
    const box = createOutlinedBox(context.newId(), image.id, imageIndex, outline, context.bounds, 0);
    if (!box || repeats(box, [...context.labeled, ...proposals])) {
      continue;
    }
    proposals.push({
      ...box,
      polygon: context.keepOutlines ? box.polygon : null,
      confidence: score,
      shelfRow: context.shelfRowAt(box.y + box.height / 2),
    });
  }
  return proposals;
}

function repeats(box: AnnotationBox, others: readonly AnnotationBox[]): boolean {
  return others.some(
    (other) => other.imageIndex === box.imageIndex && overlap(box, other) >= SAME_OBJECT_IOU,
  );
}

// Intersection over union of two boxes.
export function overlap(first: BoxGeometry, second: BoxGeometry): number {
  const width = Math.min(first.x + first.width, second.x + second.width) - Math.max(first.x, second.x);
  const height =
    Math.min(first.y + first.height, second.y + second.height) - Math.max(first.y, second.y);
  if (width <= 0 || height <= 0) {
    return 0;
  }
  const shared = width * height;
  return shared / (first.width * first.height + second.width * second.height - shared);
}
