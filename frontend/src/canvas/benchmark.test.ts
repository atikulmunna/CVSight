import { describe, expect, it, vi } from "vitest";

import { assessHost, measureFrameIntervals, summarizeTimings } from "./benchmark";

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

describe("host validity", () => {
  const summary = (p50: number, p95: number, max: number) => ({
    samples: 60,
    p50Ms: p50,
    p95Ms: p95,
    maxMs: max,
  });

  it("accepts a host that holds a steady idle cadence", () => {
    expect(assessHost(summary(16.7, 16.8, 17.4), summary(16.7, 17.1, 20))).toEqual({
      valid: true,
      reasons: [],
    });
  });

  it("rejects a run whose idle baseline already misses the limit", () => {
    const host = assessHost(summary(16.7, 41.8, 66.8), summary(16.7, 17.1, 66.8));

    expect(host.valid).toBe(false);
    expect(host.reasons.join(" ")).toContain("browser frame baseline p95 was 41.8 ms");
  });

  it("rejects a run where the canvas beat the idle baseline", () => {
    const host = assessHost(summary(16.7, 18, 19), summary(16.7, 16.8, 17));

    expect(host.valid).toBe(false);
    expect(host.reasons.join(" ")).toContain("beat the idle");
  });

  it("rejects a host that dropped frames while idle", () => {
    const host = assessHost(summary(16.7, 17, 66.9), summary(16.7, 17, 20));

    expect(host.valid).toBe(false);
    expect(host.reasons.join(" ")).toContain("dropped frames while idle");
  });

  it("reports no samples as invalid", () => {
    const empty = { samples: 0, p50Ms: 0, p95Ms: 0, maxMs: 0 };

    expect(assessHost(empty, empty).valid).toBe(false);
  });
});
