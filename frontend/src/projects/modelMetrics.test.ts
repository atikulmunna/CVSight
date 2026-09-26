import { describe, expect, it } from "vitest";

import { formatMetric, metricLabel } from "./modelMetrics";

describe("model metrics", () => {
  it("shows rates as percentages and counts as whole numbers", () => {
    expect(formatMetric("product_recall_at_iou_50", 0.997)).toBe("99.7%");
    expect(formatMetric("dense_scene_images", 0)).toBe("0");
    expect(formatMetric("overlapping_products", 8)).toBe("8");
  });

  it("says a subset recall was not measured instead of showing a number", () => {
    expect(formatMetric("dense_scene_recall_at_iou_50", null)).toBe("Not measured");
  });

  it("labels known metrics and falls back to the key", () => {
    expect(metricLabel("dense_scene_images")).toBe("Dense-scene photos");
    expect(metricLabel("custom_metric")).toBe("custom_metric");
  });
});
