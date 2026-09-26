import { ProjectImageApiError } from "./imagesApi";

export type LabeledFormat = "yolo" | "coco";
export type AnnotationState = "verified" | "proposed";

export type LabeledClass = {
  name: string;
  boxes: number;
  images: number;
  suggestedSkuId: string | null;
};

export type LabeledPreview = {
  images: number;
  boxes: number;
  skippedLabels: number;
  classes: LabeledClass[];
  sourceSplits: Record<string, number>;
};

export type LabeledOutcome = {
  path: string;
  outcome: "imported" | "completed" | "skipped" | "rejected";
  code: string;
  boxes: number;
};

export type LabeledPage = {
  results: LabeledOutcome[];
  nextOffset: number | null;
  totalImages: number;
};

export type LabeledImportRequest = {
  format: LabeledFormat;
  path: string;
  classSkus: Record<string, string | null>;
  annotationState: AnnotationState;
  offset: number;
};

export async function previewLabeledImport(
  projectId: string,
  versionId: string,
  format: LabeledFormat,
  path: string,
  fetcher: typeof fetch = fetch,
): Promise<LabeledPreview> {
  const body = record(
    await post(importsUrl(projectId, versionId, "/preview"), { format, path }, fetcher),
  );
  if (!Array.isArray(body.classes) || !body.source_splits || typeof body.source_splits !== "object") {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return {
    images: count(body.images),
    boxes: count(body.boxes),
    skippedLabels: count(body.skipped_labels),
    classes: body.classes.map((value) => {
      const item = record(value);
      return {
        name: text(item.name),
        boxes: count(item.boxes),
        images: count(item.images),
        suggestedSkuId: item.suggested_sku_id === null ? null : text(item.suggested_sku_id),
      };
    }),
    sourceSplits: Object.fromEntries(
      Object.entries(body.source_splits as Record<string, unknown>).map(([key, value]) => [
        key,
        count(value),
      ]),
    ),
  };
}

export async function runLabeledImportPage(
  projectId: string,
  versionId: string,
  request: LabeledImportRequest,
  fetcher: typeof fetch = fetch,
): Promise<LabeledPage> {
  const body = record(
    await post(
      importsUrl(projectId, versionId, ""),
      {
        format: request.format,
        path: request.path,
        class_skus: request.classSkus,
        annotation_state: request.annotationState,
        offset: request.offset,
      },
      fetcher,
    ),
  );
  if (!Array.isArray(body.results)) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return {
    results: body.results.map((value) => {
      const item = record(value);
      const outcome = item.outcome;
      if (outcome !== "imported" && outcome !== "completed" && outcome !== "skipped" && outcome !== "rejected") {
        throw new ProjectImageApiError(502, "invalid_response");
      }
      return { path: text(item.path), outcome, code: text(item.code), boxes: count(item.boxes) };
    }),
    nextOffset: body.next_offset === null ? null : count(body.next_offset),
    totalImages: count(body.total_images),
  };
}

function importsUrl(projectId: string, versionId: string, suffix: string): string {
  return (
    `/api/datasets/${encodeURIComponent(projectId)}` +
    `/versions/${encodeURIComponent(versionId)}/labeled-imports${suffix}`
  );
}

async function post(url: string, payload: unknown, fetcher: typeof fetch): Promise<unknown> {
  const response = await fetcher(url, {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const detail = body && typeof body === "object" ? (body as Record<string, unknown>).detail : null;
    const code = detail && typeof detail === "object" ? (detail as Record<string, unknown>).code : null;
    throw new ProjectImageApiError(response.status, typeof code === "string" ? code : "request_failed");
  }
  return body;
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function text(value: unknown): string {
  if (typeof value !== "string" || !value) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value;
}

function count(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return value;
}
