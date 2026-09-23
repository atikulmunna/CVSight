import { describe, expect, it, vi } from "vitest";

import { loadImageReviewed, markImageReviewed, reviewButtonState } from "./imageReview";

const IMAGE_ID = "00000000-0000-4000-8000-000000000002";

describe("image review", () => {
  it("loads and updates the explicit image review state", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response(200, { status: "reviewed" }))
      .mockResolvedValueOnce(response(200, { status: "reviewed" }));

    await expect(loadImageReviewed(IMAGE_ID, fetcher)).resolves.toBe(true);
    await expect(markImageReviewed(IMAGE_ID, fetcher)).resolves.toBeUndefined();
    expect(fetcher).toHaveBeenLastCalledWith(
      `/api/images/${IMAGE_ID}/reviewed`,
      expect.objectContaining({ method: "POST" }),
    );
  });

  it("reports an image that is not reviewed yet", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(200, { status: "in_progress" }));

    await expect(loadImageReviewed(IMAGE_ID, fetcher)).resolves.toBe(false);
  });

  it("fails when the status cannot be read", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(503, null));

    await expect(loadImageReviewed(IMAGE_ID, fetcher)).rejects.toThrow(
      "image review status is unavailable",
    );
  });

  it("explains why incomplete images cannot be marked reviewed", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(409, {}));

    await expect(markImageReviewed(IMAGE_ID, fetcher)).rejects.toThrow(
      "Accept or reject every active box first.",
    );
  });

  it("reports other review failures without detail", async () => {
    const fetcher = vi.fn().mockResolvedValue(response(500, {}));

    await expect(markImageReviewed(IMAGE_ID, fetcher)).rejects.toThrow(
      "Image review could not be saved.",
    );
  });

  it("refuses a malformed image id before any request", async () => {
    const fetcher = vi.fn();

    await expect(markImageReviewed("../images", fetcher)).rejects.toThrow("image id is invalid");
    await expect(loadImageReviewed("not-a-uuid", fetcher)).rejects.toThrow("image id is invalid");
    expect(fetcher).not.toHaveBeenCalled();
  });
});

describe("review button state", () => {
  const ready = { reviewed: false, busy: false, unresolvedCount: 0, saveStatus: "saved" as const };

  it("enables review only once every box is decided and saved", () => {
    expect(reviewButtonState(ready)).toEqual({ enabled: true, label: "Mark reviewed" });
    expect(reviewButtonState(ready, "Confirm image reviewed").label).toBe(
      "Confirm image reviewed",
    );
  });

  it("names what still blocks review", () => {
    expect(reviewButtonState({ ...ready, unresolvedCount: 3 })).toEqual({
      enabled: false,
      label: "3 decisions remaining",
    });
    expect(reviewButtonState({ ...ready, saveStatus: "saving" }).label).toBe("Saving changes");
    expect(reviewButtonState({ ...ready, saveStatus: "conflict" }).label).toBe(
      "Resolve save conflict",
    );
    expect(reviewButtonState({ ...ready, saveStatus: "offline" }).label).toBe(
      "Waiting for connection",
    );
  });

  it("stays disabled while busy or once reviewed", () => {
    expect(reviewButtonState({ ...ready, busy: true }).enabled).toBe(false);
    expect(reviewButtonState({ ...ready, reviewed: true })).toEqual({
      enabled: false,
      label: "Image reviewed",
    });
  });
});

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  };
}
