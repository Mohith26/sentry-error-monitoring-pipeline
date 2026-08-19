# Sentinel: measured results

Measured 2026-08-17 on macOS 26.4 (arm64). Python 3.12.13 + FastAPI 0.115 (in-process), SQLite (WAL), TypeScript SDK on Node v22.23.1 (targets Node 20+). All data is 100% synthetic, seeded, and reproducible: the grouping dataset (`eval/dataset.py`, seed=1337) and the mixed stream (`bench/util.py`, seed=2024).

Every number below comes from a real run, and the machine-readable values are committed under `results/*.json`. Two methodology notes apply throughout:

- Latency and throughput are measured over the ASGI app via FastAPI's `TestClient`, not over a real HTTP/TCP socket. The socket path is exercised separately by the end-to-end demo, which is functional but not timed.
- The grouping precision/recall dataset is hand-crafted with ground-truth labels, including one deliberate false split, described below.

## How to reproduce

```bash
# 0. Python env
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt

# 1. Python tests (grouping / dedupe / ingest / api / alerts / eval)
pytest -q                                  # -> 48 passed
pytest --cov=sentinel --cov-report=term-missing -q   # -> 95% coverage on sentinel/

# 2. TypeScript SDK: build + tests + capture-overhead bench
cd sdk && npm install && npm run build
npm test                                   # vitest -> 16 passed
npm run bench                              # -> results/sdk_overhead.json
cd ..

# 3. Grouping eval (precision/recall) + all Python benches -> results/*.json
python -m eval.grouping_eval               # grouping P/R vs ground truth
python -m bench.run_all                    # dedupe + throughput + latency + summary

# 4. END-TO-END DEMO (real socket): API up, SDK demo throws -> grouped issues
./scripts/run_demo.sh
#   -> "shipped 30 events -> 4 grouped issues (dedupe 7.5x)"; issue #4 is the
#      auto-captured uncaught RangeError.
```

One-command full stack (Docker):

```bash
docker compose up --build      # api (:8000) + webhook sink (:9000) + the throwing demo
# the `demo` container prints the grouped issues; alerts are delivered to the sink.
open http://localhost:8000/    # minimal dashboard   ·   /docs for the OpenAPI UI
```

## Grouping quality (`results/grouping_eval.json`)

Fingerprint each labeled event, cluster by fingerprint, compare to ground truth. Pairwise metrics are computed over all C(81,2) = 3,240 event pairs.

| Metric | Value |
|---|---|
| Events / true groups | 81 / 10 |
| Predicted clusters | 11 |
| Precision (pairwise) | 1.0000 (TP=287, FP=0) |
| Recall (pairwise) | 0.9472 (FN=16) |
| F1 | 0.9729 |
| False merges (distinct causes merged) | 0 |
| False splits (one cause split apart) | 1 |
| Homogeneity / Completeness / V-measure | 1.0000 / 0.9708 / 0.9852 |

The dataset exercises real behaviors of the engine:

- same stack, different line/column numbers -> group (line/col excluded from the hash)
- same stack, memory address / user id / email varies in the message -> group
- message-only errors whose shard#/ms/ids vary (`db-shard-7 ... 2000ms`) -> group (number/unit normalization)
- an `async ` marker on a frame -> group (async boundary must not split)
- a node_modules version differs but in-app frames match -> group (version stripped, in-app preferred)
- same exception type but different function/file -> separate
- two distinct bugs sharing the same top frame but differing deeper -> separate
- different exception types -> separate

The one false split, and why recall is below 1.0: group `G10_webhook_signature` is the same bug, but half its events carry an extra in-app retry-wrapper frame (`src/lib/retry.js`). A pure frame-signature grouper treats the extra frame as a different signature and splits the group in two. This is a real, known limitation of stack-signature grouping (inserted/entry frames shift the signature), and I left it in the dataset on purpose so the recall number reflects it rather than being a curated 1.0. `false_merges = 0` confirms the engine never conflates two distinct root causes.

## Dedupe ratio (`results/dedupe.json`)

Realistic mixed stream (seeded), 12 root causes with a skewed volume distribution and per-event line/message jitter, ingested through the store; raw events divided by resulting issues.

| Metric | Value |
|---|---|
| Root causes in stream | 12 |
| Raw events | 5,000 |
| Resulting issues | 12 |
| Dedupe ratio | 416.67x (5,000 -> 12) |

The end-to-end SDK demo (30 real thrown errors over a socket) independently yields 30 events -> 4 grouped issues, a 7.5x dedupe.

## Throughput (`results/throughput.json`)

5,000 seeded events, on-disk SQLite (WAL), measured in-process. Two paths:

| Path | Events/sec | What it includes |
|---|---|---|
| HTTP ingest (`POST /api/store` via TestClient) | 716.2 | JSON parse + pydantic validation + fingerprint + dedupe + alert eval, over the full ASGI stack (no socket) |
| Raw engine (`store_event()` direct) | 25,593.6 | fingerprint + dedupe + SQLite only |

The ~36x gap shows the grouping/store engine core is fast (~25.6k ev/s); the synchronous `TestClient` ASGI request cycle, not the engine, is the ingest bottleneck in-process. A real async uvicorn worker over a socket would sit between these two numbers, but I did not time that path (the socket demo is functional but not benchmarked).

## Latency (`results/latency.json`)

2,000 timed requests each (100 warm-up excluded), per-request `perf_counter`, on-disk SQLite, measured in-process via `TestClient` (so excluding the HTTP/TCP socket). It reflects the real per-request work: validation -> fingerprint -> SQLite dedupe -> alert eval for `/store`, and filtered/sorted SQL for `/issues`.

| Endpoint | p50 | p95 | p99 | mean |
|---|---|---|---|---|
| `POST /api/store` | 1.65 ms | 1.91 ms | 2.71 ms | 1.69 ms |
| `GET /issues` (filter+sort) | 1.94 ms | 2.38 ms | 4.16 ms | 2.03 ms |

Numbers vary a few tenths of a ms run-to-run. HTTP throughput above is the most load-sensitive number; a busier machine measured ~286 ev/s on one run vs 716 here.

## SDK per-capture overhead (`results/sdk_overhead.json`)

20,000 iterations, error thrown from 3 levels deep (9 stack frames/event), Node v22.23.1, in-memory transport (no network). Per-call wall time via `performance.now()`.

| Path | p50 | p95 | mean |
|---|---|---|---|
| `eventFromException` (stack parse + normalize + envelope build) | 0.0099 ms | 0.0157 ms | 0.0117 ms |
| `captureException` (+ transport.send to memory) | 0.0098 ms | 0.0135 ms | 0.0126 ms |

So building a full normalized event envelope from a thrown `Error` costs about 0.012 ms (~12 µs) per capture, excluding network transport.

## Tests

| Suite | Command | Result |
|---|---|---|
| Python | `pytest -q` | 48 passed |
| Python coverage | `pytest --cov=sentinel` | 95% on `sentinel/` |
| SDK (vitest) | `cd sdk && npm test` | 16 passed |
| Total | | 64 passing |

Python tests cover grouping correctness (same-cause variants collapse, distinct causes stay separate, normalization units, configurable in-app rules), store dedupe (count increments, first/last_seen, project scoping, idempotent re-delivery), ingest validation (bad event_id/level/timestamp/missing-payload -> 422), query filter/sort, resolve/ignore, alerts (new-issue + threshold crossing + webhook delivery), and the eval guardrails. SDK tests cover stack parsing (function/file/lineno/colno, oldest-first ordering, in-app detection, node_modules/node: exclusion, anonymous + async frames, a real runtime stack) and the client (envelope shape, captureException/Message, breadcrumbs + capping).

## Notes and limitations

- Synthetic, seeded data throughout; no real production error stream.
- The grouping dataset is hand-crafted with ground-truth labels and contains 1 deliberate false split (retry-wrapper frame), so the 0.947 recall reflects a real limitation instead of a curated score.
- Latency and throughput are in-process (`TestClient`), not over a network socket. The socket path is proven functional by the end-to-end demo (`scripts/run_demo.sh`) but is not timed.
- End-to-end was verified over a real socket via `scripts/run_demo.sh` (API on `127.0.0.1:8000` + the compiled SDK demo): 30 thrown errors -> 4 grouped issues, the uncaught `RangeError` auto-captured, and `new_issue` + `threshold` alerts fired. The `docker compose` stack (api + webhook sink + demo) is provided and valid; base-image registry pulls were intermittent where I measured, so the socket demo is the verified end-to-end path here.
- HTTP throughput is machine-load-sensitive (see the latency section); the raw-engine number is the more stable measure of the grouping/store core.
- Frame-signature grouping is sensitive to inserted/entry frames. That is the same reason the demo yields down to a uniform async context (see the comment in `sdk/demo/demo.ts`), and the reason production error monitors layer additional stack-trace normalization rules on top of this approach.
- The store is SQLite by choice: local, zero external services, byte-reproducible. All SQL is isolated in `sentinel/store.py`; a Postgres swap would touch only that file.
- Not built: source-map symbolication for minified JS, per-project rate limiting/quotas, spike-detection alerts, and any production UI framework.
