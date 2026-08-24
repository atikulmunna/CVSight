import { describe, expect, it, vi } from "vitest";

import {
  loadGapReviewManifest,
  loadGapReviewImageStatus,
  markGapReviewImage,
  parseGapReviewManifest,
} from "./gapReview";

describe("gap review manifest", () => {
  it("parses the frozen 50-image navigation contract", () => {
    const parsed = parseGapReviewManifest(manifest());

    expect(parsed.items).toHaveLength(50);
    expect(parsed.reviewMode).toBe("candidate-review");
    expect(parsed.items[0]).toMatchObject({
      position: 1,
      split: "validation",
      candidateCount: 0,
    });
    expect(parsed.items[49]?.split).toBe("test");
  });

  it("rejects duplicate image identifiers", () => {
    const value = manifest();
    value.items[1]!.image_id = value.items[0]!.image_id;

    expect(() => parseGapReviewManifest(value)).toThrow("must be unique");
  });

  it("recognizes a blind ground-truth manifest", () => {
    const value = manifest();
    value.review_mode = "blind-truth";

    expect(parseGapReviewManifest(value).reviewMode).toBe("blind-truth");
  });

  it("only loads manifests from the local fixture directory", async () => {
    const fetcher = vi.fn();

    await expect(loadGapReviewManifest("/api/private", fetcher)).rejects.toThrow(
      "path is invalid",
    );
    expect(fetcher).not.toHaveBeenCalled();
  });

  it("loads and updates the explicit image review state", async () => {
    const imageId = "00000000-0000-4000-8000-000000000002";
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response(200, { status: "reviewed" }))
      .mockResolvedValueOnce(response(200, { status: "reviewed" }));

    await expect(loadGapReviewImageStatus(imageId, fetcher)).resolves.toBe(true);
    await expect(markGapReviewImage(imageId, fetcher)).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenLastCalledWith(
      `/api/images/${imageId}/reviewed`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("explains why incomplete images cannot be marked reviewed", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(409, {}));

    await expect(
      markGapReviewImage("00000000-0000-4000-8000-000000000002", fetcher),
    ).rejects.toThrow("Accept or reject every active box first.");
  });
});

function manifest() {
  return {
    schema: "cvsight-gap-review-navigation/v1",
    review_mode: undefined as string | undefined,
    dataset_version_id: "00000000-0000-4000-8000-000000000001",
    items: Array.from({ length: 50 }, (_, index) => ({
      position: index + 1,
      image_id: `00000000-0000-4000-8000-${String(index + 2).padStart(12, "0")}`,
      source_image_id: `source-${index + 1}`,
      name: `image-${index + 1}.jpg`,
      split: index < 20 ? "validation" : "test",
      capture_group: `capture-${index + 1}`,
      candidate_count: index % 4,
    })),
  };
}

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}
