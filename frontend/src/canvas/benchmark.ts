export type TimingSummary = {
  samples: number;
  p50Ms: number;
  p95Ms: number;
  maxMs: number;
};

export type CanvasBenchmark = {
  browserFrameBaseline: TimingSummary;
  frameInterval: TimingSummary;
  selectionUpdate: TimingSummary;
  inputLatency: TimingSummary;
  measuredAt: string;
};

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
