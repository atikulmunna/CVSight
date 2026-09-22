export type TimingSummary = {
  samples: number;
  p50Ms: number;
  p95Ms: number;
  maxMs: number;
};

export type HostValidity = {
  valid: boolean;
  reasons: string[];
};

export type CanvasBenchmark = {
  browserFrameBaseline: TimingSummary;
  frameInterval: TimingSummary;
  selectionUpdate: TimingSummary;
  inputLatency: TimingSummary;
  measuredAt: string;
  host: HostValidity;
};

// The browser frame baseline measures an empty animation loop, so it is the
// control: when the host cannot hold a steady cadence with no canvas work, the
// application timings from the same run describe the machine, not the canvas.
export const BASELINE_P95_LIMIT_MS = 20;
export const BASELINE_MAX_FRAME_MULTIPLE = 2;

export function assessHost(
  baseline: TimingSummary,
  frameInterval: TimingSummary,
): HostValidity {
  const reasons: string[] = [];
  if (baseline.samples === 0) {
    reasons.push("the browser frame baseline recorded no samples");
  }
  if (baseline.p95Ms > BASELINE_P95_LIMIT_MS) {
    reasons.push(
      `the browser frame baseline p95 was ${baseline.p95Ms} ms with no canvas work, ` +
        `above the ${BASELINE_P95_LIMIT_MS} ms limit`,
    );
  }
  const framePeriod = baseline.p50Ms > 0 ? baseline.p50Ms : 16.7;
  if (baseline.maxMs > framePeriod * BASELINE_MAX_FRAME_MULTIPLE) {
    reasons.push(
      `the browser dropped frames while idle, worst interval ${baseline.maxMs} ms`,
    );
  }
  // Canvas work cannot cost less than an idle loop, so a faster canvas measurement
  // means a stray hitch landed in whichever phase happened to be running.
  if (frameInterval.samples > 0 && frameInterval.p95Ms + 1 < baseline.p95Ms) {
    reasons.push(
      `the canvas frame interval p95 (${frameInterval.p95Ms} ms) beat the idle ` +
        `baseline (${baseline.p95Ms} ms), so the run was disturbed`,
    );
  }
  return { valid: reasons.length === 0, reasons };
}

export function summarizeTimings(values: number[]): TimingSummary {
  if (values.length === 0) {
    return { samples: 0, p50Ms: 0, p95Ms: 0, maxMs: 0 };
  }
  return {
    samples: values.length,
    p50Ms: round(percentile(values, 50)),
    p95Ms: round(percentile(values, 95)),
    maxMs: round(Math.max(...values)),
  };
}

export async function measureFrameIntervals(
  samples = 90,
  pulse?: (index: number, active: boolean) => void,
): Promise<number[]> {
  const intervals: number[] = [];
  let previous = await nextFrame();
  for (let index = 0; index < samples; index += 1) {
    pulse?.(index, true);
    try {
      const current = await nextFrame();
      intervals.push(current - previous);
      previous = current;
    } finally {
      pulse?.(index, false);
    }
  }
  return intervals;
}

export function nextFrame(): Promise<number> {
  return new Promise((resolve) => requestAnimationFrame(resolve));
}

function percentile(values: number[], requestedPercentile: number): number {
  const sorted = [...values].sort((left, right) => left - right);
  const index = Math.min(
    sorted.length - 1,
    Math.max(0, Math.ceil((requestedPercentile / 100) * sorted.length) - 1),
  );
  return sorted[index]!;
}

function round(value: number): number {
  return Number(value.toFixed(3));
}
