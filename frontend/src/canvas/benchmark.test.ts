import { describe, expect, it, vi } from "vitest";

import { measureFrameIntervals, summarizeTimings } from "./benchmark";

describe("canvas benchmark summaries", () => {
  it("reports p50, p95, and maximum values", () => {
    expect(summarizeTimings([1, 2, 3, 4, 50])).toEqual({
      samples: 5,
      p50Ms: 3,
      p95Ms: 50,
      maxMs: 50,
    });
  });

  it("handles an empty measurement", () => {
    expect(summarizeTimings([])).toEqual({
      samples: 0,
      p50Ms: 0,
      p95Ms: 0,
      maxMs: 0,
    });
  });

  it("activates and clears the canvas pulse for each measured frame", async () => {
    let timestamp = 0;
    vi.stubGlobal("requestAnimationFrame", (callback: FrameRequestCallback) => {
      timestamp += 16;
      callback(timestamp);
      return timestamp;
    });
    const pulses: Array<[number, boolean]> = [];

    await expect(
      measureFrameIntervals(2, (index, active) => {
        pulses.push([index, active]);
      }),
    ).resolves.toEqual([16, 16]);
    expect(pulses).toEqual([
      [0, true],
      [0, false],
      [1, true],
      [1, false],
    ]);
  });
});
