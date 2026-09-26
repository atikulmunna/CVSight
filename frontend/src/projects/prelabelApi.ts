import { loadProjectImages, ProjectImageApiError } from "./imagesApi";
import { loadModelDeployment } from "./modelsApi";

export type PrelabelSummary = {
  queued: number;
  alreadyQueued: number;
  failedEarlier: number;
  rejected: number;
};

const PAGE_SIZE = 250;

// One key per version and promoted detector: repeated clicks find the jobs already
// queued instead of adding duplicate proposals, and promoting a different detector lets
// photos that are still unlabeled be tried again.
export function prelabelKey(versionId: string, detectorEntryId: string): string {
  return `ui-prelabel:${versionId}:${detectorEntryId}`;
}

export async function prelabelUnlabeledImages(
  projectId: string,
  versionId: string,
  fetcher: typeof fetch = fetch,
): Promise<PrelabelSummary> {
  // Only the promoted detector may pre-label, so without one nothing would be stored.
  const deployment = await loadModelDeployment("known_sku_detector", fetcher);
  if (!deployment) {
    throw new ProjectImageApiError(409, "no_promoted_detector");
  }
  const imageIds: string[] = [];
  for (let offset = 0; ; offset += PAGE_SIZE) {
    const page = await loadProjectImages(projectId, versionId, "unlabeled", offset, PAGE_SIZE, fetcher);
    imageIds.push(...page.images.map((image) => image.id));
    if (page.images.length < PAGE_SIZE || imageIds.length >= page.total) {
      break;
    }
  }
  const summary: PrelabelSummary = { queued: 0, alreadyQueued: 0, failedEarlier: 0, rejected: 0 };
  for (let start = 0; start < imageIds.length; start += PAGE_SIZE) {
    const response = await fetcher("/api/prelabels/batch", {
      method: "POST",
      headers: { Accept: "application/json", "Content-Type": "application/json" },
      body: JSON.stringify({
        image_ids: imageIds.slice(start, start + PAGE_SIZE),
        idempotency_key: prelabelKey(versionId, deployment.active.id),
      }),
    });
    const body: unknown = await response.json().catch(() => null);
    if (!response.ok) {
      throw new ProjectImageApiError(response.status, errorCode(body));
    }
    for (const item of items(body)) {
      if (item.status === "queued") {
        summary.queued += 1;
      } else if (item.status === "deduplicated") {
        if (item.job_state === "failed") {
          summary.failedEarlier += 1;
        } else {
          summary.alreadyQueued += 1;
        }
      } else {
        summary.rejected += 1;
      }
    }
  }
  return summary;
}

export async function liveWorkerCount(fetcher: typeof fetch = fetch): Promise<number> {
  const response = await fetcher("/api/workers/health", {
    headers: { Accept: "application/json" },
  });
  const body: unknown = await response.json().catch(() => null);
  if (!body || typeof body !== "object" || !Array.isArray((body as Record<string, unknown>).workers)) {
    throw new ProjectImageApiError(response.status, errorCode(body));
  }
  return ((body as Record<string, unknown>).workers as unknown[]).filter(
    (worker) => worker && typeof worker === "object" && (worker as Record<string, unknown>).stale === false,
  ).length;
}

function items(value: unknown): Array<Record<string, unknown>> {
  const list = value && typeof value === "object" ? (value as Record<string, unknown>).items : null;
  if (!Array.isArray(list)) {
    throw new ProjectImageApiError(502, "invalid_response");
  }
  return list.filter((item): item is Record<string, unknown> => Boolean(item) && typeof item === "object");
}

function errorCode(value: unknown): string {
  const detail = value && typeof value === "object" ? (value as Record<string, unknown>).detail : null;
  const code = detail && typeof detail === "object" ? (detail as Record<string, unknown>).code : null;
  return typeof code === "string" ? code : "request_failed";
}
