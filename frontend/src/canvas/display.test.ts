import { afterEach, describe, expect, it } from "vitest";

import {
  DEFAULT_DISPLAY,
  hiddenBoxClasses,
  imageFilter,
  loadDisplaySettings,
  saveDisplaySettings,
} from "./display";

const STORAGE_KEY = "shelfsight:canvas-display";

afterEach(() => {
  localStorage.clear();
});

describe("display settings", () => {
  it("starts from the defaults when nothing usable is stored", () => {
    expect(loadDisplaySettings()).toEqual(DEFAULT_DISPLAY);

    localStorage.setItem(STORAGE_KEY, "{not json");
    expect(loadDisplaySettings()).toEqual(DEFAULT_DISPLAY);

    localStorage.setItem(STORAGE_KEY, JSON.stringify(["brightness", 150]));
    expect(loadDisplaySettings()).toEqual(DEFAULT_DISPLAY);
  });

  it("keeps stored settings and drops values out of range or unknown", () => {
    const settings = {
      brightness: 150,
      contrast: 80,
      boxFill: 30,
      hiddenClasses: ["gap" as const],
    };
    saveDisplaySettings(settings);
    expect(loadDisplaySettings()).toEqual(settings);

    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({
        brightness: 900,
        contrast: "120",
        boxFill: Number.NaN,
        hiddenClasses: ["shelf_label", "hats", 4],
      }),
    );
    expect(loadDisplaySettings()).toEqual({
      ...DEFAULT_DISPLAY,
      hiddenClasses: ["shelf_label"],
    });
  });

  it("leaves the photo unfiltered at the default brightness and contrast", () => {
    expect(imageFilter(DEFAULT_DISPLAY)).toBeUndefined();
    expect(imageFilter({ ...DEFAULT_DISPLAY, brightness: 150 })).toBe(
      "brightness(1.5) contrast(1)",
    );
  });

  it("names the hidden classes for the stylesheet, or every box when all are hidden", () => {
    const settings = { ...DEFAULT_DISPLAY, hiddenClasses: ["gap" as const, "shelf_label" as const] };

    expect(hiddenBoxClasses(DEFAULT_DISPLAY, false)).toBe("");
    expect(hiddenBoxClasses(settings, false)).toBe(" hides-gap hides-shelf_label");
    expect(hiddenBoxClasses(settings, true)).toBe(" hides-all");
  });
});
