import { useState } from "react";

import { localBoxId } from "./editing";
import { nearestShelfRow, type AnnotationBox, type CanvasFixture } from "./model";
import { requestSmartFind, smartBoxMessage } from "./smartBoxApi";
import { foundProposals } from "./smartFind";

// The API takes at most this many example boxes.
export const MAX_FIND_EXAMPLES = 8;

type SmartFindOptions = {
  fixture: CanvasFixture;
  activeAnnotations: AnnotationBox[];
  outlinesProducts: boolean;
  // Receives the new proposals, all at once, so they undo in one step.
  onFound: (boxes: AnnotationBox[]) => void;
};

// Asks SAM for every product on the photo that matches a phrase, example boxes, or both,
// and hands back the ones not labeled yet as proposals. One find runs at a time.
export function useSmartFind({
  fixture,
  activeAnnotations,
  outlinesProducts,
  onFound,
}: SmartFindOptions) {
  const [pending, setPending] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  // The examples are product boxes on one photo; the find runs on that photo.
  async function find(text: string, examples: readonly AnnotationBox[]) {
    if (pending) {
      return;
    }
    const imageIndex = examples[0]?.imageIndex ?? 0;
    const image = fixture.images[imageIndex]!;
    setPending(true);
    setMessage(null);
    try {
      const found = await requestSmartFind(image.id, {
        text: text.trim() || null,
        exemplars: examples
          .filter((box) => box.imageIndex === imageIndex)
          .slice(0, MAX_FIND_EXAMPLES)
          .map((box) => ({
            x: box.x - image.x,
            y: box.y - image.y,
            width: box.width,
            height: box.height,
          })),
      });
      const proposals = foundProposals(found, {
        image,
        imageIndex,
        bounds: fixture,
        labeled: activeAnnotations,
        keepOutlines: outlinesProducts,
        newId: () => localBoxId("found"),
        shelfRowAt: (y) => nearestShelfRow(activeAnnotations, y),
      });
      onFound(proposals);
      setMessage(
        found.length === 0
          ? "SAM found nothing. Try other words or example boxes."
          : `SAM found ${found.length}; ${proposals.length} ${
              proposals.length === 1 ? "is a new proposal" : "are new proposals"
            }.`,
      );
    } catch (error) {
      setMessage(smartBoxMessage(error, "Find"));
    } finally {
      setPending(false);
    }
  }

  return { pending, message, find };
}
