import { useState, type RefObject } from "react";
import { flushSync } from "react-dom";

import {
  assessHost,
  measureFrameIntervals,
  nextFrame,
  summarizeTimings,
  type CanvasBenchmark,
} from "./benchmark";
import type { AnnotationBox } from "./model";

type CanvasBenchmarkOptions = {
  annotations: AnnotationBox[];
  selectedId: string | null;
  // Selection goes through the same state a reviewer's clicks use, so the timings are
  // the canvas's real ones.
  select: (id: string | null) => void;
  viewportRef: RefObject<HTMLDivElement | null>;
};

// Measures frame, selection, and input timings on the loaded canvas, and restores the
// selection afterwards.
export function useCanvasBenchmark({
  annotations,
  selectedId,
  select,
  viewportRef,
}: CanvasBenchmarkOptions) {
  const [benchmark, setBenchmark] = useState<CanvasBenchmark | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [running, setRunning] = useState(false);

  async function run() {
    if (running || annotations.length === 0) {
      return;
    }
    setRunning(true);
    setBenchmark(null);
    setError(null);
    const originalSelection = selectedId;
    try {
      const browserFrameBaseline = await measureFrameIntervals(60);
      const annotationNodes = Array.from(
        viewportRef.current?.querySelectorAll<SVGGElement>(".annotation") ?? [],
      );
      const frameIntervals = await measureFrameIntervals(90, (index, active) => {
        const annotation = annotationNodes[(index * 19) % annotationNodes.length];
        annotation?.classList.toggle("benchmark-pulse", active);
      });
      const selectionUpdates: number[] = [];
      for (let index = 0; index < 200; index += 1) {
        const annotation = annotations[index % annotations.length]!;
        const startedAt = performance.now();
        flushSync(() => select(annotation.id));
        selectionUpdates.push(performance.now() - startedAt);
        if ((index + 1) % 5 === 0) {
          await nextFrame();
        }
      }

      const inputLatencies: number[] = [];
      for (let index = 0; index < 45; index += 1) {
        const annotation = annotations[index % annotations.length]!;
        const startedAt = performance.now();
        flushSync(() => select(annotation.id));
        await nextFrame();
        inputLatencies.push(performance.now() - startedAt);
      }
      const baselineSummary = summarizeTimings(browserFrameBaseline);
      const frameSummary = summarizeTimings(frameIntervals);
      setBenchmark({
        browserFrameBaseline: baselineSummary,
        frameInterval: frameSummary,
        selectionUpdate: summarizeTimings(selectionUpdates),
        inputLatency: summarizeTimings(inputLatencies),
        measuredAt: new Date().toISOString(),
        host: assessHost(baselineSummary, frameSummary),
      });
    } catch {
      setError("Measurement failed. Reload the page and try again.");
    } finally {
      select(originalSelection);
      setRunning(false);
    }
  }

  return { benchmark, error, running, run };
}
