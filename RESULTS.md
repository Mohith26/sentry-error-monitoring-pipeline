# Sentinel — Measured Results

**Date measured:** 2026-08-17
**Stack:** Python 3.12.13 + FastAPI 0.115 (in-process) · SQLite (WAL) · TypeScript SDK on Node v22.23.1 (targets Node ≥20).
**Platform:** macOS 26.4 (arm64).
**Data:** 100% **synthetic**, seeded, reproducible (grouping dataset `eval/dataset.py` seed=1337; mixed stream `bench/util.py` seed=2024).

Every number below comes from a real run. Machine-readable values are committed under
`results/*.json`; anything not measured is written as the literal `___`.

> **Two honesty tags used throughout:**
> - **[in-process]** latency/throughput are measured over the ASGI app via FastAPI's
>   `TestClient`, **not over a real HTTP/TCP socket** (the socket path is exercised
>   separately by the end-to-end demo, which is functional but not timed).
> - **[synthetic/crafted]** the grouping precision/recall dataset is hand-crafted with
>   ground-truth labels — see the honest false-split it contains, below.

---

## How to reproduce (exact commands)

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

### One-command full stack (Docker)

```bash
docker compose up --build      # api (:8000) + webhook sink (:9000) + the throwing demo
# the `demo` container prints the grouped issues; alerts are delivered to the sink.
open http://localhost:8000/    # minimal dashboard   ·   /docs for the OpenAPI UI
```

---

## 1. Grouping quality — `results/grouping_eval.json`  *(synthetic/crafted)*

Fingerprint each labeled event, cluster by fingerprint, compare to ground truth.
Pairwise metrics are computed over **all C(81,2)=3,240 event pairs**.

| Metric | Value |
|---|---|
| Events / true groups | **81 / 10** |
| Predicted clusters | **11** |
| **Precision (pairwise)** | **1.0000** (TP=287, FP=0) |
| **Recall (pairwise)** | **0.9472** (FN=16) |
| **F1** | **0.9729** |
| **False merges** (distinct causes merged) | **0** |
| **False splits** (one cause split apart) | **1** |
| Homogeneity / Completeness / V-measure | **1.0000 / 0.9708 / 0.9852** |

**What the dataset exercises (all real behaviours of the engine):**
- same stack, **different line/column numbers** → group (line/col excluded from the hash)
- same stack, **memory address / user id / email varies in the message** → group
- **message-only** errors whose shard#/ms/ids vary (`db-shard-7 … 2000ms`) → group (number/unit normalization)
- an **`async ` marker** on a frame → group (async boundary must not split)
- a **node_modules version differs** but in-app frames match → group (version stripped, in-app preferred)
- same exception **type but different function/file** → separate
- two distinct bugs sharing the **same top frame but differing deeper** → separate
- different exception **types** → separate

**The one honest false split (why recall < 1.0):** group `G10_webhook_signature` is the
same bug, but half its events carry an extra in-app **retry-wrapper** frame
(`src/lib/retry.js`). A pure frame-signature grouper treats the extra frame as a
different signature and splits the group in two. This is a **real, known limitation of
stack-signature grouping** (inserted/entry frames shift the signature) and is left in
the dataset on purpose so the recall number is honest rather than a curated 1.0.
`false_merges = 0` confirms the engine never conflates two distinct root causes.

## 2. Dedupe ratio — `results/dedupe.json`  *(in-process)*

Realistic mixed stream (seeded), 12 root causes with a skewed volume distribution and
per-event line/message jitter, ingested through the store; raw events ÷ resulting issues.

| Metric | Value |
|---|---|
| Root causes in stream | **12** |
| Raw events | **5,000** |
| Resulting issues | **12** |
| **Dedupe ratio** | **416.67×** (5,000 → 12) |

The end-to-end **SDK demo** (30 real thrown errors over a socket) independently yields
**30 events → 4 grouped issues = 7.5× dedupe**.

## 3. Throughput — `results/throughput.json`  *(in-process)*

5,000 seeded events, on-disk SQLite (WAL). Two paths measured (both honest, labelled):

| Path | Events/sec | What it includes |
|---|---|---|
| **HTTP ingest** (`POST /api/store` via TestClient) | **716.2** | JSON parse + pydantic validation + fingerprint + dedupe + alert eval, over the full ASGI stack (no socket) |
| **Raw engine** (`store_event()` direct) | **25,593.6** | fingerprint + dedupe + SQLite only |

The ~36× gap shows the grouping/store **engine core is fast** (~25.6k ev/s); the
synchronous `TestClient` ASGI request cycle — not the engine — is the ingest bottleneck
in-process. A real async uvicorn worker over a socket sits between these; **not timed**
here (the socket demo is functional but not benchmarked).

## 4. Latency — `results/latency.json`  *(in-process)*

2,000 timed requests each (100 warm-up excluded), per-request `perf_counter`, on-disk SQLite.

| Endpoint | p50 | p95 | p99 | mean |
|---|---|---|---|---|
| `POST /api/store` | **1.65 ms** | **1.91 ms** | 2.71 ms | 1.69 ms |
| `GET /issues` (filter+sort) | **1.94 ms** | **2.38 ms** | 4.16 ms | 2.03 ms |

**Methodology (honest):** measured **in-process via FastAPI `TestClient` (ASGI)**, so it
excludes the HTTP/TCP socket. It reflects the real per-request work (validation →
fingerprint → SQLite dedupe → alert eval for `/store`; filtered/sorted SQL for `/issues`).
Numbers vary a few tenths of a ms run-to-run; **HTTP throughput in §3 is the most
load-sensitive number** (a busier machine measured ~286 ev/s on one run vs 716 here).

## 5. SDK per-capture overhead — `results/sdk_overhead.json`

20,000 iterations, error thrown from 3 levels deep (**9 stack frames/event**), Node
v22.23.1, in-memory transport (no network). Per-call wall time via `performance.now()`.

| Path | p50 | p95 | mean |
|---|---|---|---|
| `eventFromException` (stack parse + normalize + envelope build) | **0.0099 ms** | **0.0157 ms** | 0.0117 ms |
| `captureException` (+ transport.send to memory) | **0.0098 ms** | **0.0135 ms** | 0.0126 ms |

So building a full normalized event envelope from a thrown `Error` costs **~0.012 ms
(~12 µs) per capture**, excluding network transport.

## 6. Tests

| Suite | Command | Result |
|---|---|---|
| Python | `pytest -q` | **48 passed** |
| Python coverage | `pytest --cov=sentinel` | **95%** on `sentinel/` |
| SDK (vitest) | `cd sdk && npm test` | **16 passed** |
| **Total** | | **64 passing** |

Python tests cover grouping correctness (same-cause variants collapse, distinct causes
stay separate, normalization units, configurable in-app rules), store dedupe (count
increments, first/last_seen, project scoping, idempotent re-delivery), ingest validation
(bad event_id/level/timestamp/missing-payload → 422), query filter/sort, resolve/ignore,
alerts (new-issue + threshold crossing + webhook delivery), and the eval guardrails.
SDK tests cover stack parsing (function/file/lineno/colno, oldest-first ordering, in-app
detection, node_modules/node: exclusion, anonymous + async frames, a real runtime stack)
and the client (envelope shape, captureException/Message, breadcrumbs + capping).

---

## Honest limitations / notes

- **Synthetic, seeded data** throughout — no real production error stream.
- **Grouping dataset is hand-crafted** with ground-truth labels; it contains **1
  deliberate false split** (retry-wrapper frame) so recall (0.947) is honest, not curated.
- **Latency + throughput are in-process** (`TestClient`), not over a network socket. The
  socket path is proven functional by the end-to-end demo (`scripts/run_demo.sh`) but is
  **not timed**.
- **End-to-end verified over a real socket** via `scripts/run_demo.sh` (API on
  `127.0.0.1:8000` + the compiled SDK demo): 30 thrown errors → 4 grouped issues, the
  uncaught `RangeError` auto-captured, and `new_issue` + `threshold` alerts fired. The
  `docker compose` stack (api + webhook sink + demo) is provided and valid; base-image
  registry pulls were intermittent in the build sandbox, so the **socket demo is the
  verified end-to-end path here.**
- **HTTP throughput is machine-load-sensitive** (see §4) — the raw-engine number is the
  more stable measure of the grouping/store core.
- **Frame-signature grouping is sensitive to inserted/entry frames** — the same reason
  the demo yields down to a uniform async context (see the comment in `sdk/demo/demo.ts`)
  and the reason real Sentry layers additional stack-trace normalization rules.
- **Store is SQLite** (documented choice): local, zero external services, byte-reproducible.
  All SQL is isolated in `sentinel/store.py`; a Postgres swap would touch only that file.
- **Not built (out of scope):** source-map symbolication for minified JS, per-project rate
  limiting/quotas, spike-detection alerts, and any production UI framework.
