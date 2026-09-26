import { describe, expect, it, vi } from "vitest";

import { ProjectImageApiError } from "./imagesApi";
import { PROMOTED_DETECTOR_ID, promotedDetectorDeployment } from "./modelFixtures";
import { liveWorkerCount, prelabelKey, prelabelUnlabeledImages } from "./prelabelApi";

const PROJECT_ID = "11111111-1111-4111-8111-111111111111";
const VERSION_ID = "22222222-2222-4222-8222-222222222222";
const DEPLOYMENT_URL = "/api/model-deployments/known_sku_detector";

describe("pre-label queueing", () => {
  it("pages through unlabeled photos, queues them in batches, and tallies outcomes", async () => {
    const ids = Array.from({ length: 300 }, (_, index) => imageId(index));
    const posted: string[][] = [];
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === DEPLOYMENT_URL) {
        return json(200, promotedDetectorDeployment());
      }
      if (url === "/api/prelabels/batch") {
        const body = JSON.parse(String(init?.body)) as { image_ids: string[]; idempotency_key: string };
        expect(body.idempotency_key).toBe(prelabelKey(VERSION_ID, PROMOTED_DETECTOR_ID));
        posted.push(body.image_ids);
        return json(202, {
          items: body.image_ids.map((id, index) => ({
            image_id: id,
            status: index === 0 ? "deduplicated" : index === 1 ? "rejected" : "queued",
            job_state: index === 0 && posted.length === 2 ? "failed" : "queued",
          })),
        });
      }
      const offset = Number(new URL(url, "http://local").searchParams.get("offset"));
      expect(url).toContain("status=unlabeled");
      return json(200, page(ids.slice(offset, offset + 250), ids.length, offset));
    });

    const summary = await prelabelUnlabeledImages(PROJECT_ID, VERSION_ID, fetcher);

    expect(posted.map((batch) => batch.length)).toEqual([250, 50]);
    expect(posted.flat()).toEqual(ids);
    expect(summary).toEqual({ queued: 296, alreadyQueued: 1, failedEarlier: 1, rejected: 2 });
  });

  it("queues nothing when every photo already has labels", async () => {
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      String(input) === DEPLOYMENT_URL
        ? json(200, promotedDetectorDeployment())
        : json(200, page([], 0, 0)),
    );

    await expect(prelabelUnlabeledImages(PROJECT_ID, VERSION_ID, fetcher)).resolves.toEqual({
      queued: 0,
      alreadyQueued: 0,
      failedEarlier: 0,
      rejected: 0,
    });
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("refuses to queue anything until a detector is promoted", async () => {
    const fetcher = vi.fn().mockReturnValue(json(404, { detail: { code: "model_deployment_not_found" } }));

    await expect(prelabelUnlabeledImages(PROJECT_ID, VERSION_ID, fetcher)).rejects.toEqual(
      new ProjectImageApiError(409, "no_promoted_detector"),
    );
    expect(fetcher).toHaveBeenCalledTimes(1);
  });

  it("reports a refused batch with its status and code", async () => {
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      String(input) === DEPLOYMENT_URL
        ? json(200, promotedDetectorDeployment())
        : String(input) === "/api/prelabels/batch"
          ? json(403, { detail: { code: "forbidden" } })
          : json(200, page([imageId(1)], 1, 0)),
    );

    await expect(prelabelUnlabeledImages(PROJECT_ID, VERSION_ID, fetcher)).rejects.toEqual(
      new ProjectImageApiError(403, "forbidden"),
    );
  });

  it("counts only workers with a fresh heartbeat", async () => {
    const fetcher = vi.fn().mockReturnValue(
      json(200, { status: "ok", workers: [{ stale: false }, { stale: true }, { stale: false }] }),
    );

    await expect(liveWorkerCount(fetcher)).resolves.toBe(2);
  });
});

function imageId(index: number): string {
  return `00000000-0000-4000-8000-${String(index + 1).padStart(12, "0")}`;
}

function page(ids: string[], total: number, offset: number) {
  return {
    images: ids.map((id) => ({
      id,
      original_filename: `${id}.jpg`,
      media_type: "image/jpeg",
      width: 40,
      height: 20,
      status: "unlabeled",
      created_at: "2026-09-26T08:00:00Z",
      thumbnail_url: `/api/images/${id}/media/thumbnail`,
    })),
    total,
    limit: 250,
    offset,
  };
}

function json(status: number, body: unknown) {
  return Promise.resolve({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });
}
