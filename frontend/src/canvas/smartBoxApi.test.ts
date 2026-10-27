import { describe, expect, it, vi } from "vitest";

import {
  prepareSmartTool,
  requestSmartBox,
  requestSmartFind,
  requestSmartPolygon,
  SmartBoxError,
  smartBoxMessage,
} from "./smartBoxApi";

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

  it("preloads a photo once and holds a click until the preload is done", async () => {
    let finishPreload: (value: unknown) => void = () => undefined;
    const fetcher = vi.fn().mockImplementation((input: RequestInfo | URL) =>
      String(input).endsWith("/smart-prepare")
        ? new Promise((resolve) => {
            finishPreload = resolve;
          })
        : Promise.resolve(response(200, { x: 10, y: 12, width: 30, height: 40, score: 0.9 })),
    );

    prepareSmartTool("image-9", fetcher);
    prepareSmartTool("image-9", fetcher);
    const box = requestSmartBox("image-9", { x: 25, y: 30 }, fetcher);
    await Promise.resolve();
    expect(fetcher).toHaveBeenCalledTimes(1);

    finishPreload(response(204, null));
    await expect(box).resolves.toMatchObject({ x: 10 });
    expect(fetcher).toHaveBeenNthCalledWith(
      1,
      "/api/images/image-9/smart-prepare",
      expect.objectContaining({ method: "POST" }),
    );
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("lets a click go ahead after a failed preload", async () => {
    const fetcher = vi
      .fn()
      .mockRejectedValueOnce(new TypeError("offline"))
      .mockResolvedValue(response(200, { x: 10, y: 12, width: 30, height: 40, score: 0.9 }));

    prepareSmartTool("image-8", fetcher);

    await expect(requestSmartBox("image-8", { x: 25, y: 30 }, fetcher)).resolves.toMatchObject({
      x: 10,
    });
  });

  it("finds objects by phrase and example boxes and rejects a malformed answer", async () => {
    const outline = [[10, 12], [40, 12], [25, 52]];
    const fetcher = vi
      .fn()
      .mockResolvedValueOnce(response(200, { objects: [{ polygon: outline, score: 0.7 }] }))
      .mockResolvedValueOnce(response(200, { objects: [{ polygon: outline, score: 2 }] }));
    const prompt = { text: "bottle", exemplars: [{ x: 1, y: 2, width: 3, height: 4 }] };

    await expect(requestSmartFind("image 1", prompt, fetcher)).resolves.toEqual([
      { polygon: outline, score: 0.7 },
    ]);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/images/image%201/smart-find",
      expect.objectContaining({ method: "POST", body: JSON.stringify(prompt) }),
    );
    await expect(requestSmartFind("image 1", prompt, fetcher)).rejects.toMatchObject({
      code: "invalid_response",
    });
  });

  it("posts every smart polygon click and returns the outline in photo pixels", async () => {
    const outline = [[10, 12], [40, 12], [25, 52]];
    const fetcher = vi.fn().mockResolvedValue(response(200, { polygon: outline, score: 0.8 }));
    const clicks = [
      { x: 25, y: 30, include: true },
      { x: 30, y: 45, include: false },
    ];

    await expect(requestSmartPolygon("image 1", clicks, fetcher)).resolves.toEqual(outline);
    expect(fetcher).toHaveBeenCalledWith(
      "/api/images/image%201/smart-polygon",
      expect.objectContaining({ method: "POST", body: JSON.stringify({ points: clicks }) }),
    );
  });

  it("rejects a malformed outline and names the smart polygon in its messages", async () => {
    const malformed = vi
      .fn()
      .mockResolvedValue(response(200, { polygon: [[1, 1], [2, 2]], score: 0.8 }));
    const click = [{ x: 1, y: 1, include: true }];

    await expect(requestSmartPolygon("image-1", click, malformed)).rejects.toMatchObject({
      code: "invalid_response",
    });
    expect(smartBoxMessage(new SmartBoxError(502, "segmenter_outdated"), "Smart polygon")).toBe(
      "The SAM service is out of date. Update it to use Smart polygon.",
    );
    expect(smartBoxMessage(new SmartBoxError(422, "no_object"), "Smart polygon")).toBe(
      "No object found at that point.",
    );
    expect(smartBoxMessage(new TypeError("offline"), "Smart polygon")).toBe(
      "Smart polygon failed. Try again.",
    );
  });
});
