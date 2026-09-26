const METRIC_LABELS: Record<string, string> = {
  map_50_95: "mAP 50 to 95",
  product_recall_at_iou_50: "Product recall",
  duplicate_rate_at_iou_50: "Duplicate rate",
  dense_scene_recall_at_iou_50: "Dense-scene recall",
  dense_scene_images: "Dense-scene photos",
  overlapping_product_recall_at_iou_50: "Overlapping recall",
  overlapping_products: "Overlapping products",
};

// Counts of evaluated photos or boxes, shown as whole numbers rather than rates.
const COUNT_METRICS = new Set(["dense_scene_images", "overlapping_products"]);

export function metricLabel(key: string): string {
  return METRIC_LABELS[key] ?? key;
}

export function formatMetric(key: string, value: number | null): string {
  // The registry accepts an empty subset recall only when the evaluation had no such subset.
  if (value === null) {
    return "Not measured";
  }
  if (COUNT_METRICS.has(key)) {
    return value.toLocaleString();
  }
  if (value >= 0 && value <= 1) {
    return `${(value * 100).toFixed(1)}%`;
  }
  return value.toLocaleString(undefined, { maximumFractionDigits: 3 });
}
