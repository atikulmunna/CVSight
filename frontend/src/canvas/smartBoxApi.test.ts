import { describe, expect, it, vi } from "vitest";

import { requestSmartBox, SmartBoxError, smartBoxMessage } from "./smartBoxApi";

function response(status: number, body: unknown) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: vi.fn().mockResolvedValue(body),
  } as unknown as Response;
}

describe("smart box API client", () => {
  it("posts the point and returns the box in photo pixels", async () => {
    const fetcher = vi
      .fn()
      .mockResolvedValue(
        response(200, { x: 10, y: 12, width: 30, height: 40, score: 0.9 }),
      );

    await expect(requestSmartBox("image 1", { x: 25.5, y: 30 }, fetcher)).resolves.toEqual({
      x: 10,
      y: 12,
      width: 30,
      height: 40,
    });
    expect(fetcher).toHaveBeenCalledWith(
      "/api/images/image%201/smart-box",
      expect.objectContaining({
        method: "POST",
        body: JSON.stringify({ x: 25.5, y: 30 }),
      }),
    );
  });

  it("keeps the API's error code and rejects malformed boxes", async () => {
    const unavailable = vi
      .fn()
      .mockResolvedValue(response(503, { detail: { code: "segmenter_unavailable" } }));
    const malformed = vi
      .fn()
      .mockResolvedValue(response(200, { x: 1, y: 1, width: 0, height: 4 }));

    await expect(requestSmartBox("image-1", { x: 1, y: 1 }, unavailable)).rejects.toMatchObject({
      status: 503,
      code: "segmenter_unavailable",
    });
    await expect(requestSmartBox("image-1", { x: 1, y: 1 }, malformed)).rejects.toMatchObject({
      code: "invalid_response",
    });
  });

  it("explains each failure in words a reviewer can act on", () => {
    expect(smartBoxMessage(new SmartBoxError(503, "segmenter_unavailable"))).toBe(
      "Smart box could not reach the SAM service. Is the tunnel open?",
    );
    expect(smartBoxMessage(new SmartBoxError(422, "no_object"))).toMatch(/No object found/);
    expect(smartBoxMessage(new TypeError("offline"))).toBe(
      "Smart box failed. Try again or draw the box.",
    );
  });
});
