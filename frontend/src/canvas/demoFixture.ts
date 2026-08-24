import type {
  AnnotationBox,
  AnnotationClass,
  CanvasFixture,
  OverlayState,
} from "./model";

const SCENE_WIDTH = 1_920;
const SCENE_HEIGHT = 1_080;
const ROWS = 8;
const COLUMNS = 40;
const BOX_WIDTH = 38;
const BOX_HEIGHT = 102;
const HORIZONTAL_GAP = 8;
const VERTICAL_GAP = 24;
const LEFT = 42;
const TOP = 54;

const SKU_NAMES = [
  "Aurora Cola Classic",
  "Aurora Cola Zero",
  "Verde Sparkling Lime",
  "Nimbus Still Water",
  "Harvest Oat Crisps",
  "Kettle Sea Salt Chips",
  "Meadow Whole Milk",
  "Sunfield Orange Juice",
];

function boxState(index: number): OverlayState {
  if (index % 29 === 0) {
    return "flagged";
  }
  if (index % 17 === 0) {
    return "propagated";
  }
  return index % 5 === 0 ? "unverified" : "verified";
}

function boxClass(index: number): AnnotationClass {
  if (index % 73 === 0) {
    return "gap";
  }
  if (index % 97 === 0) {
    return "shelf_label";
  }
  return "product";
}

function createAnnotations(): AnnotationBox[] {
  return Array.from({ length: ROWS * COLUMNS }, (_, index) => {
    const row = Math.floor(index / COLUMNS);
    const column = index % COLUMNS;
    const classType = boxClass(index);
    const state = boxState(index);
    return {
      id: `B${String(index + 1).padStart(3, "0")}`,
      serverId: null,
      imageId: null,
      revision: null,
      x: LEFT + column * (BOX_WIDTH + HORIZONTAL_GAP),
      y: TOP + row * (BOX_HEIGHT + VERTICAL_GAP),
      width: BOX_WIDTH,
      height: classType === "shelf_label" ? 28 : BOX_HEIGHT,
      classType,
      state,
      lifecycleState: state === "verified" ? "verified" : "proposed",
      reviewState:
        state === "verified"
          ? "accepted"
          : state === "flagged"
            ? "flagged"
            : "unreviewed",
      sku: classType === "gap" ? "Visible gap" : SKU_NAMES[index % SKU_NAMES.length]!,
      skuId: null,
      confidence: classType === "gap" ? null : 0.62 + ((index * 13) % 37) / 100,
      occluded: false,
      truncated: false,
      shelfRow: row,
      imageIndex: 0,
    };
  });
}

export const DEMO_FIXTURE: CanvasFixture = {
  name: "Dense shelf demonstration",
  width: SCENE_WIDTH,
  height: SCENE_HEIGHT,
  images: [
    {
      id: "demo-shelf",
      url: "/demo-shelf.svg",
      x: 0,
      y: 0,
      width: SCENE_WIDTH,
      height: SCENE_HEIGHT,
    },
  ],
  annotations: createAnnotations(),
};
