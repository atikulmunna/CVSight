import { describe, expect, it } from "vitest";

import type { AnnotationBox } from "./model";
import { parseCanvasFixture, spatialReadingOrder } from "./model";

function box(id: string, x: number, y: number): AnnotationBox {
  return {
    id,
    serverId: null,
    imageId: null,
    revision: null,
    x,
    y,
    width: 20,
    height: 40,
    classType: "product",
    state: "verified",
    lifecycleState: "verified",
    reviewState: "accepted",
    sku: id,
    skuId: null,
    confidence: 0.9,
    occluded: false,
    truncated: false,
    shelfRow: null,
    imageIndex: 0,
  };
}

function fixture(boxes: Array<Record<string, unknown>>) {
  return {
    image: { width: 200, height: 100 },
    images: [{ id: "image", url: "/image.jpg", x: 0, y: 0, width: 200, height: 100 }],
    boxes,
  };
}

describe("canvas model", () => {
  it("orders boxes by visual row and then left to right", () => {
    const ordered = spatialReadingOrder([
      box("bottom-right", 80, 50),
      box("top-right", 80, 3),
      box("bottom-left", 10, 48),
      box("top-left", 10, 5),
    ]);

    expect(ordered.map((item) => item.id)).toEqual([
      "top-left",
      "top-right",
      "bottom-left",
      "bottom-right",
    ]);
  });

  it("validates and maps the local real-fixture contract", () => {
    const parsed = parseCanvasFixture(
      fixture([
        {
          id: "box-1",
          x: 10,
          y: 10,
          width: 20,
          height: 30,
          kind: "product",
          state: "unverified",
          sku: "Test SKU",
          confidence: 0.8,
          image_index: 0,
        },
      ]),
    );

    expect(parsed.annotations[0]).toMatchObject({
      id: "box-1",
      classType: "product",
      state: "unverified",
    });
  });

  it.each([
    [
      fixture([
        {
          id: "bad-box",
          x: 190,
          y: 10,
          width: 20,
          height: 30,
          kind: "product",
          state: "verified",
          image_index: 0,
        },
      ]),
      "exceeds fixture bounds",
    ],
    [
      {
        ...fixture([]),
        images: [
          {
            id: "image",
            url: "https://example.com/image.jpg",
            x: 0,
            y: 0,
            width: 200,
            height: 100,
          },
        ],
        boxes: [
          {
            id: "box",
            x: 1,
            y: 1,
            width: 10,
            height: 10,
            kind: "product",
            state: "verified",
            image_index: 0,
          },
        ],
      },
      "same-origin absolute path",
    ],
  ])("rejects invalid external fixture data", (value, message) => {
    expect(() => parseCanvasFixture(value)).toThrow(message);
  });
});
