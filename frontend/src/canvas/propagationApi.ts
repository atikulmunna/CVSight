import { mapAnnotationResponse } from "./annotationApi";
import type { AnnotationBox } from "./model";

type Fetcher = typeof fetch;

export type HardPair = {
  skuId: string;
  name: string;
  brand: string | null;
  variant: string | null;
  reason: string;
};

export type PropagationCandidate = {
  suggestionId: string;
  annotationId: string;
  annotationRevision: number;
  imageId: string;
  imageUrl: string;
  imageWidth: number;
  imageHeight: number;
  x: number;
  y: number;
  width: number;
  height: number;
  score: number;
  requiresIndividualReview: boolean;
  riskReason: "hard_pair" | null;
};

export type PropagationSuggestionSet = {
  suggestionSetId: string;
  seedSku: {
    skuId: string;
    name: string;
    brand: string | null;
    variant: string | null;
  };
  hardPairs: HardPair[];
  candidates: PropagationCandidate[];
  qualityEvidence: string;
};

export type PropagationConfirmation = {
  suggestionSetId: string;
  selectedCount: number;
  skippedCount: number;
  annotations: AnnotationBox[];
};

export class PropagationApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
  ) {
    super("propagation request failed");
  }
}

export async function queuePropagationEmbeddings(
  annotations: AnnotationBox[],
  fetcher: Fetcher = fetch,
): Promise<{ queued: number; rejected: number }> {
  const annotationIds = annotations
    .map((annotation) => annotation.serverId)
    .filter((annotationId): annotationId is string => annotationId !== null);
  const body = await request(
    "/api/propagation/embeddings",
    {
      method: "POST",
      headers: jsonHeaders(),
      body: JSON.stringify({ annotation_ids: annotationIds }),
    },
    fetcher,
  );
  const results = array(record(body).results, "queue results");
  return {
    queued: results.filter((item) => {
      const status = string(record(item).status, "queue status");
      return status === "queued" || status === "deduplicated";
    }).length,
    rejected: results.filter(
      (item) => string(record(item).status, "queue status") === "rejected",
    ).length,
  };
}

export async function createPropagationSuggestions(
  seed: AnnotationBox,
  fetcher: Fetcher = fetch,
): Promise<PropagationSuggestionSet> {
  if (!seed.serverId || seed.revision === null) {
    throw new PropagationApiError(400, "missing_server_identity");
  }
  const body = record(
    await request(
      `/api/annotations/${encodeURIComponent(seed.serverId)}/propagation-suggestions`,
      {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({
          expected_revision: seed.revision,
          top_k: 18,
        }),
      },
      fetcher,
    ),
  );
  const seedSku = record(body.seed_sku);
  const index = record(body.index);
  return {
    suggestionSetId: string(body.suggestion_set_id, "suggestion set id"),
    seedSku: {
      skuId: string(seedSku.sku_id, "seed SKU id"),
      name: string(seedSku.name, "seed SKU name"),
      brand: nullableString(seedSku.brand),
      variant: nullableString(seedSku.variant),
    },
    hardPairs: array(body.hard_pairs, "hard pairs").map(parseHardPair),
    candidates: array(body.candidates, "propagation candidates").map(
      parseCandidate,
    ),
    qualityEvidence: string(index.quality_evidence, "quality evidence"),
  };
}

export async function confirmPropagationSuggestions(
  suggestionSet: PropagationSuggestionSet,
  selectedIds: Set<string>,
  reviewedIds: Set<string>,
  localAnnotations: AnnotationBox[],
  fetcher: Fetcher = fetch,
): Promise<PropagationConfirmation> {
  const body = record(
    await request(
      `/api/propagation/suggestion-sets/${encodeURIComponent(
        suggestionSet.suggestionSetId,
      )}/confirm`,
      {
        method: "POST",
        headers: jsonHeaders(),
        body: JSON.stringify({
          decisions: suggestionSet.candidates.map((candidate) => ({
            suggestion_id: candidate.suggestionId,
            decision: selectedIds.has(candidate.suggestionId)
              ? "confirm"
              : "skip",
            reviewed_individually: reviewedIds.has(candidate.suggestionId),
          })),
        }),
      },
      fetcher,
    ),
  );
  const previousByServerId = new Map(
    localAnnotations
      .filter((annotation) => annotation.serverId !== null)
      .map((annotation) => [annotation.serverId, annotation]),
  );
  const annotations = array(body.annotations, "confirmed annotations").map(
    (value) => {
      const response = record(value);
      const previous = previousByServerId.get(
        string(response.id, "annotation id"),
      );
      if (!previous) {
        throw new PropagationApiError(502, "unknown_annotation_response");
      }
      return mapAnnotationResponse(response, {
        ...previous,
        sku: suggestionSet.seedSku.name,
      });
    },
  );
  return {
    suggestionSetId: string(body.suggestion_set_id, "suggestion set id"),
    selectedCount: nonNegativeInteger(body.selected_count, "selected count"),
    skippedCount: nonNegativeInteger(body.skipped_count, "skipped count"),
    annotations,
  };
}

function parseHardPair(value: unknown): HardPair {
  const pair = record(value);
  return {
    skuId: string(pair.sku_id, "hard-pair SKU id"),
    name: string(pair.name, "hard-pair SKU name"),
    brand: nullableString(pair.brand),
    variant: nullableString(pair.variant),
    reason: string(pair.reason, "hard-pair reason"),
  };
}

function parseCandidate(value: unknown): PropagationCandidate {
  const candidate = record(value);
  const riskReason = candidate.risk_reason;
  if (riskReason !== null && riskReason !== "hard_pair") {
    throw new PropagationApiError(502, "invalid_risk_reason");
  }
  return {
    suggestionId: string(candidate.suggestion_id, "suggestion id"),
    annotationId: string(candidate.annotation_id, "candidate annotation id"),
    annotationRevision: positiveInteger(
      candidate.annotation_revision,
      "candidate revision",
    ),
    imageId: string(candidate.image_id, "candidate image id"),
    imageUrl: safeImageUrl(candidate.image_url),
    imageWidth: positiveInteger(candidate.image_width, "candidate image width"),
    imageHeight: positiveInteger(
      candidate.image_height,
      "candidate image height",
    ),
    x: nonNegativeNumber(candidate.x, "candidate x"),
    y: nonNegativeNumber(candidate.y, "candidate y"),
    width: positiveNumber(candidate.width, "candidate width"),
    height: positiveNumber(candidate.height, "candidate height"),
    score: boundedNumber(candidate.score, "candidate score", -1, 1),
    requiresIndividualReview:
      candidate.requires_individual_review === true,
    riskReason,
  };
}

async function request(
  url: string,
  init: RequestInit,
  fetcher: Fetcher,
): Promise<unknown> {
  const response = await fetcher(url, init);
  const body: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    throw new PropagationApiError(response.status, errorCode(body));
  }
  return body;
}

function jsonHeaders(): Record<string, string> {
  return {
    Accept: "application/json",
    "Content-Type": "application/json",
  };
}

function errorCode(value: unknown): string {
  if (!value || typeof value !== "object") {
    return "request_failed";
  }
  const detail = (value as Record<string, unknown>).detail;
  if (!detail || typeof detail !== "object") {
    return "request_failed";
  }
  const code = (detail as Record<string, unknown>).code;
  return typeof code === "string" ? code : "request_failed";
}

function record(value: unknown): Record<string, unknown> {
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new PropagationApiError(502, "invalid_response");
  }
  return value as Record<string, unknown>;
}

function array(value: unknown, label: string): unknown[] {
  if (!Array.isArray(value)) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return value;
}

function string(value: unknown, label: string): string {
  if (typeof value !== "string" || value === "") {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return value;
}

function nullableString(value: unknown): string | null {
  return value === null ? null : string(value, "nullable string");
}

function finiteNumber(value: unknown, label: string): number {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return value;
}

function positiveNumber(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (number <= 0) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return number;
}

function nonNegativeNumber(value: unknown, label: string): number {
  const number = finiteNumber(value, label);
  if (number < 0) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return number;
}

function boundedNumber(
  value: unknown,
  label: string,
  minimum: number,
  maximum: number,
): number {
  const number = finiteNumber(value, label);
  if (number < minimum || number > maximum) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return number;
}

function positiveInteger(value: unknown, label: string): number {
  const number = positiveNumber(value, label);
  if (!Number.isInteger(number)) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return number;
}

function nonNegativeInteger(value: unknown, label: string): number {
  const number = nonNegativeNumber(value, label);
  if (!Number.isInteger(number)) {
    throw new PropagationApiError(502, `invalid_${label.replaceAll(" ", "_")}`);
  }
  return number;
}

function safeImageUrl(value: unknown): string {
  const url = string(value, "candidate image URL");
  if (!url.startsWith("/api/images/") || !url.endsWith("/media/canonical")) {
    throw new PropagationApiError(502, "invalid_candidate_image_url");
  }
  return url;
}
