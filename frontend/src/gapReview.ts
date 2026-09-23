export type GapReviewItem = {
  position: number;
  imageId: string;
  sourceImageId: string;
  name: string;
  split: "validation" | "test";
  captureGroup: string;
  candidateCount: number;
};

export type GapReviewManifest = {
  datasetVersionId: string;
  reviewMode: "candidate-review" | "blind-truth";
  items: GapReviewItem[];
};

export async function loadGapReviewManifest(
  path = "/local-fixtures/gap-review.json",
  fetcher: typeof fetch = fetch,
): Promise<GapReviewManifest> {
  if (!path.startsWith("/local-fixtures/") || path.includes("..") || path.includes("\\")) {
    throw new Error("gap review manifest path is invalid");
  }
  const response = await fetcher(path, {
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error("gap review manifest is unavailable");
  }
  return parseGapReviewManifest(await response.json());
}

export function parseGapReviewManifest(value: unknown): GapReviewManifest {
  const body = record(value);
  if (
    body.schema !== "cvsight-gap-review-navigation/v1" ||
    !Array.isArray(body.items) ||
    body.items.length !== 50
  ) {
    throw new Error("gap review manifest is invalid");
  }
  const items = body.items.map((value, index) => parseItem(value, index));
  if (new Set(items.map((item) => item.imageId)).size !== items.length) {
    throw new Error("gap review image identifiers must be unique");
  }
  return {
    datasetVersionId: uuid(body.dataset_version_id),
    reviewMode:
      body.review_mode === "blind-truth" ? "blind-truth" : "candidate-review",
    items,
  };
}

function parseItem(value: unknown, index: number): GapReviewItem {
  const item = record(value);
  const position = integer(item.position, "position");
  if (position !== index + 1) {
    throw new Error("gap review positions must be contiguous");
  }
  if (item.split !== "validation" && item.split !== "test") {
    throw new Error("gap review split is invalid");
  }
  return {
    position,
    imageId: uuid(item.image_id),
    sourceImageId: text(item.source_image_id, "source image id"),
    name: text(item.name, "image name"),
    split: item.split,
    captureGroup: text(item.capture_group, "capture group"),
    candidateCount: integer(item.candidate_count, "candidate count", true),
  };
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error("gap review value must be an object");
  }
  return value as Record<string, unknown>;
}

function text(value: unknown, label: string): string {
  if (typeof value !== "string" || !value.trim()) {
    throw new Error(`${label} must be a non-empty string`);
  }
  return value;
}

function uuid(value: unknown): string {
  const parsed = text(value, "UUID");
  if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[1-8][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(parsed)) {
    throw new Error("gap review UUID is invalid");
  }
  return parsed;
}

function integer(value: unknown, label: string, allowZero = false): number {
  if (!Number.isInteger(value) || (value as number) < (allowZero ? 0 : 1)) {
    throw new Error(`${label} is invalid`);
  }
  return value as number;
}
