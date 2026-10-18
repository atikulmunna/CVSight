import type { CanvasBenchmark } from "./benchmark";

export function BenchmarkOutput({
  benchmark,
  error,
}: {
  benchmark: CanvasBenchmark | null;
  error: string | null;
}) {
  if (error) {
    return (
      <p className="benchmark-output benchmark-error" role="alert">
        {error}
      </p>
    );
  }
  if (!benchmark) {
    return (
      <p className="benchmark-output">
        Measure p50, p95, and maximum frame and selection timings with the loaded
        fixture.
      </p>
    );
  }
  return (
    <div className="benchmark-output" data-testid="benchmark-results">
      {!benchmark.host.valid && (
        <p className="benchmark-invalid" role="alert" data-testid="benchmark-invalid">
          <strong>Run not valid on this machine.</strong> The timings below describe the
          host, not the canvas: {benchmark.host.reasons.join("; ")}. Close other
          applications and measure again.
        </p>
      )}
      <dl>
      <Metric
        label="Browser frame baseline"
        value={benchmark.browserFrameBaseline}
      />
      <Metric label="Canvas frame interval" value={benchmark.frameInterval} />
      <Metric label="Selection update" value={benchmark.selectionUpdate} />
      <Metric label="Input latency" value={benchmark.inputLatency} />
      <div>
        <dt>Measured at</dt>
        <dd data-testid="benchmark-timestamp">
          {new Date(benchmark.measuredAt).toLocaleTimeString()}
        </dd>
      </div>
      <div>
        <dt>Host</dt>
        <dd data-testid="benchmark-host">{benchmark.host.valid ? "Valid" : "Not valid"}</dd>
      </div>
      </dl>
    </div>
  );
}

function Metric({
  label,
  value,
}: {
  label: string;
  value: { p50Ms: number; p95Ms: number; maxMs: number };
}) {
  return (
    <div>
      <dt>{label}</dt>
      <dd>
        {value.p50Ms} / {value.p95Ms} / {value.maxMs} ms
      </dd>
    </div>
  );
}
