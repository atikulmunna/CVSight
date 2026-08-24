import type { Sku, SkuReferenceImage, SkuStatus } from "./model";

export type SkuFields = {
  name: string;
  upc: string | null;
  category: string | null;
  subcategory: string | null;
  brand: string | null;
  variant: string | null;
};

export class CatalogApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("catalog request failed");
  }
}

export async function loadCatalog(fetcher: typeof fetch = fetch): Promise<Sku[]> {
  const response = await fetcher("/api/skus?status=all&limit=2500", {
    headers: { Accept: "application/json" },
  });
  const body = await responseBody(response);
  if (!response.ok) {
    throw apiError(response.status, body);
  }
  const record = requireRecord(body);
  if (!Array.isArray(record.skus)) {
    throw new CatalogApiError(502, "invalid_response");
  }
  return record.skus.map(parseSku);
}

export async function createSku(
  fields: SkuFields,
  fetcher: typeof fetch = fetch,
): Promise<Sku> {
  return skuRequest("/api/skus", "POST", fields, fetcher);
}

export async function updateSku(
  skuId: string,
  fields: SkuFields,
  fetcher: typeof fetch = fetch,
): Promise<Sku> {
  return skuRequest(
    `/api/skus/${encodeURIComponent(skuId)}`,
    "PUT",
    fields,
    fetcher,
  );
}

export async function deprecateSku(
  skuId: string,
  fetcher: typeof fetch = fetch,
): Promise<Sku> {
  return skuRequest(
    `/api/skus/${encodeURIComponent(skuId)}/deprecate`,
    "POST",
    undefined,
    fetcher,
  );
}

export async function mergeSku(
  sourceId: string,
  targetId: string,
  fetcher: typeof fetch = fetch,
): Promise<{ source: Sku; target: Sku; repointedAnnotations: number }> {
  const response = await writeRequest(
    `/api/skus/${encodeURIComponent(sourceId)}/merge`,
    "POST",
    { target_sku_id: targetId },
    fetcher,
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw apiError(response.status, body);
  }
  const record = requireRecord(body);
  return {
    source: parseSku(record.source),
    target: parseSku(record.target),
    repointedAnnotations: requireNonNegativeInteger(
      record.repointed_annotations,
    ),
  };
}

export async function uploadReferenceImage(
  skuId: string,
  file: File,
  fetcher: typeof fetch = fetch,
): Promise<SkuReferenceImage> {
  const form = new FormData();
  form.append("file", file);
  const response = await fetcher(
    `/api/skus/${encodeURIComponent(skuId)}/reference-images`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
      body: form,
    },
  );
  const body = await responseBody(response);
  if (!response.ok) {
    throw apiError(response.status, body);
  }
  return parseReference(body);
}

async function skuRequest(
  url: string,
  method: "POST" | "PUT",
  body: SkuFields | undefined,
  fetcher: typeof fetch,
): Promise<Sku> {
  const response = await writeRequest(url, method, body, fetcher);
  const value = await responseBody(response);
  if (!response.ok) {
    throw apiError(response.status, value);
  }
  return parseSku(value);
}

function writeRequest(
  url: string,
  method: "POST" | "PUT",
  body: unknown,
  fetcher: typeof fetch,
): Promise<Response> {
  return fetcher(url, {
    method,
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

function parseSku(value: unknown): Sku {
  const record = requireRecord(value);
  const references = Array.isArray(record.reference_images)
    ? record.reference_images.map(parseReference)
    : invalidResponse();
  return {
    id: requireString(record.id),
    name: requireString(record.name),
    upc: nullableString(record.upc),
    category: nullableString(record.category),
    subcategory: nullableString(record.subcategory),
    brand: nullableString(record.brand),
    variant: nullableString(record.variant),
    isUnknown: requireBoolean(record.is_unknown),
    status: requireStatus(record.status),
    mergedIntoId: nullableString(record.merged_into_id),
    referenceImages: references,
  };
}

function parseReference(value: unknown): SkuReferenceImage {
  const record = requireRecord(value);
  return {
    id: requireString(record.id),
    originalFilename: requireString(record.original_filename),
    thumbnailUrl: requireSafeUrl(record.thumbnail_url),
  };
}

function apiError(status: number, value: unknown): CatalogApiError {
  if (!value || typeof value !== "object") {
    return new CatalogApiError(status, "request_failed");
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object") {
    return new CatalogApiError(status, "request_failed");
  }
  const code = (detail as Record<string, unknown>).code;
  return new CatalogApiError(
    status,
    typeof code === "string" ? code : "request_failed",
  );
}

async function responseBody(response: Response): Promise<unknown> {
  return response.json().catch(() => null);
}

function requireRecord(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    return invalidResponse();
  }
  return value as Record<string, unknown>;
}

function requireString(value: unknown): string {
  if (typeof value !== "string" || value === "") {
    return invalidResponse();
  }
  return value;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : requireString(value);
}

function requireBoolean(value: unknown): boolean {
  if (typeof value !== "boolean") {
    return invalidResponse();
  }
  return value;
}

function requireStatus(value: unknown): SkuStatus {
  if (value !== "active" && value !== "deprecated" && value !== "merged") {
    return invalidResponse();
  }
  return value;
}

function requireSafeUrl(value: unknown): string {
  const url = requireString(value);
  if (!url.startsWith("/") || url.startsWith("//") || url.includes("..")) {
    return invalidResponse();
  }
  return url;
}

function requireNonNegativeInteger(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value) || value < 0) {
    return invalidResponse();
  }
  return value;
}

function invalidResponse(): never {
  throw new CatalogApiError(502, "invalid_response");
}
