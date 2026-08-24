# Performance validation

CVSight validates its release scale with a disposable PostgreSQL database and a fixed,
deterministic fixture. The benchmark exercises actual HTTP requests against Uvicorn, the
production SQLAlchemy services, exact pgvector similarity, job claiming, and both export formats.

## Run the backend gate

Docker must be running. From the repository root, run:

```powershell
.\scripts\performance-check.ps1
```

The script builds the project database image, starts an isolated database on a random loopback
port, applies every migration, runs the benchmark, and removes its temporary database and media.
It retains the JSON evidence at `benchmark-local/t037-scale-report.json`. A different report path
can be supplied with `-Output`.

The fixed workload contains:

- 100,000 image metadata records
- 2,000 active catalog SKUs plus the required Unknown SKU
- one image with 300 accepted boxes
- 1,000 queued jobs processed by four concurrent workers
- 2,000 exact recognition vectors queried by eight concurrent clients
- a frozen 250-image snapshot with 3,000 accepted annotations
- concurrent successful and expected-failure HTTP requests
- repeated detection and recognition exports

Thresholds are constants in `benchmark_tool/scale.py` and are evaluated without adapting them to
the observed result. The report contains p50, p95, maximum latency, unexpected response counts,
throughput, database growth, Python traced memory, platform details, and the result of every gate.
A process error stops the run instead of being counted as a successful timing sample.

Database growth is measured before seeding, after seeding, and after the workload. Python memory
uses `tracemalloc` around the job, similarity, and export workloads. HTTP latency runs without that
instrumentation because allocation tracing materially changes response serialization time. The
memory result excludes PostgreSQL memory, native image buffers, and operating-system caches. Run
the gate on the target release machine when comparing results across releases.

## Run the canvas gate

The browser benchmark uses the production dense fixture with at least 300 boxes. Start the local
API and frontend, open the fixture in a Chromium browser, and select **Measure** in the Performance
panel. Do not switch tabs or resize the window during the sample.

Record three runs after one warm-up run. The release gates use the worst p95 from the three runs:

| Measurement | p95 gate |
| --- | ---: |
| Browser frame baseline | 20 ms or less |
| Canvas frame interval | 25 ms or less |
| Selection update | 8 ms or less |
| Input latency | 25 ms or less |

The maximum is retained as diagnostic evidence. A repeated maximum above 50 ms should be
investigated even when the p95 gate passes. The benchmark is a responsiveness check, not a GPU
model-inference measurement.
