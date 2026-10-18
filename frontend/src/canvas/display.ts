import type { AnnotationClass } from "./model";

// How the canvas looks to this reviewer. None of it changes annotations or the photo.
export type DisplaySettings = {
  brightness: number;
  contrast: number;
  boxFill: number;
  hiddenClasses: AnnotationClass[];
};

export const DEFAULT_DISPLAY: DisplaySettings = {
  brightness: 100,
  contrast: 100,
  boxFill: 0,
  hiddenClasses: [],
};

// Percent ranges for the sliders, also used to reject stored values out of range.
export const DISPLAY_RANGES = {
  brightness: { min: 50, max: 200 },
  contrast: { min: 50, max: 200 },
  boxFill: { min: 0, max: 60 },
} as const;

const STORAGE_KEY = "shelfsight:canvas-display";
const CLASSES: readonly AnnotationClass[] = ["product", "gap", "shelf_label"];

// The workspace remounts for every photo, so settings are kept in this browser to
// carry from one photo to the next.
export function loadDisplaySettings(): DisplaySettings {
  try {
    const value: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) ?? "null");
    if (!value || typeof value !== "object" || Array.isArray(value)) {
      return DEFAULT_DISPLAY;
    }
    const stored = value as Record<string, unknown>;
    const hidden: unknown[] = Array.isArray(stored.hiddenClasses) ? stored.hiddenClasses : [];
    return {
      brightness: percent(stored.brightness, "brightness"),
      contrast: percent(stored.contrast, "contrast"),
      boxFill: percent(stored.boxFill, "boxFill"),
      hiddenClasses: CLASSES.filter((classType) => hidden.includes(classType)),
    };
  } catch {
    return DEFAULT_DISPLAY;
  }
}

export function saveDisplaySettings(settings: DisplaySettings): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(settings));
  } catch {
    // Without storage the settings still apply to this photo; nothing else depends on them.
  }
}

export function imageFilter(settings: DisplaySettings): string | undefined {
  if (settings.brightness === 100 && settings.contrast === 100) {
    return undefined;
  }
  return `brightness(${settings.brightness / 100}) contrast(${settings.contrast / 100})`;
}

// Classes for the scene element; the stylesheet hides matching boxes unless selected.
export function hiddenBoxClasses(settings: DisplaySettings, hideAll: boolean): string {
  if (hideAll) {
    return " hides-all";
  }
  return settings.hiddenClasses.map((classType) => ` hides-${classType}`).join("");
}

function percent(value: unknown, field: keyof typeof DISPLAY_RANGES): number {
  const { min, max } = DISPLAY_RANGES[field];
  return typeof value === "number" && Number.isFinite(value) && value >= min && value <= max
    ? value
    : DEFAULT_DISPLAY[field];
}
