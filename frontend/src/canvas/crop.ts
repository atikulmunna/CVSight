import type { CSSProperties } from "react";

import type { AnnotationBox, FixtureImage } from "./model";

// A box shown on its own by drawing the whole photo, scaled, as a background centred on
// the box. The browser decodes the photo once however many crops show it.
export function cropStyle(
  box: AnnotationBox,
  image: FixtureImage,
  frame: { width: number; height: number },
  scale: number,
): CSSProperties {
  const centerX = box.x - image.x + box.width / 2;
  const centerY = box.y - image.y + box.height / 2;
  return {
    backgroundImage: `url("${image.url}")`,
    backgroundSize: `${image.width * scale}px ${image.height * scale}px`,
    backgroundPosition: `${-centerX * scale + frame.width / 2}px ${
      -centerY * scale + frame.height / 2
    }px`,
  };
}
