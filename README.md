# Sentinel

Sentinel is a miniature error-monitoring pipeline I built end to end: a TypeScript SDK captures uncaught exceptions and `captureException` calls with a normalized stack trace and context, then POSTs an event envelope to a Python/FastAPI ingest API, which fingerprints the stack trace to group same-root-cause events into one Issue, stores events, and exposes a query and alerting API with a minimal dashboard.

The part I cared most about is issue grouping, the same fingerprint-then-group approach production error monitors use: turning a flood of raw error events into a deduplicated set of actionable issues by normalizing stack frames and collapsing the volatile parts. Everything else (SDK, store, alerts, dashboard) exists so that algorithm runs inside a realistic pipeline, and the whole thing is benchmarked for grouping precision/recall, dedupe ratio, ingest throughput, and query latency.

## How grouping works

The fingerprinter (`sentinel/grouping.py`) hashes the frame signature (per frame: normalized module + function), preferring in-app frames, with line/column numbers excluded so "same stack, different build" groups together. It falls back to exception type + normalized message, then to the message alone. Message normalization strips the volatile parts: memory addresses, hex, UUIDs, integers (including things like `2000ms`), quoted user data, emails. So `User 12345 not found` and `User 67890 not found` collapse into one issue. Fingerprints are deterministic (SHA-1), and the in-app frame rules are configurable.

## Pipeline

```
  ┌────────────────────┐   POST /api/store    ┌──────────────────────────────────────┐
  │  TypeScript SDK     │   { event envelope } │        Python ingest API (FastAPI)     │
  │  (sdk/, Node ≥20)   │ ───────────────────▶ │                                        │
  │  • uncaught handler │                      │  models    pydantic validate envelope  │
  │  • captureException │                      │  grouping  normalize frames -> SHA-1    │
  │  • stack parse      │                      │            fingerprint (in-app rules)   │
  │  • release/env/tags │                      │  store     dedupe by (project,fp) ->    │
  │  • breadcrumbs      │                      │            Issue{first/last_seen,count} │
  └────────────────────┘                      │  alerts    new-issue + threshold -> hook │
                                               │  api       /issues (filter/sort),       │
   GET /issues · /issues/{id}  ◀────────────── │            /issues/{id}, resolve/ignore, │
   resolve/ignore · /alerts · / (dashboard)    │            /alerts, / dashboard          │
                                               └──────────────────────────────────────┘
```

## Running it

```bash
# Python
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q                                    # 48 passed

# TypeScript SDK
cd sdk && npm install && npm run build && npm test   # vitest: 16 passed
cd ..

# Measure everything -> results/*.json
python -m eval.grouping_eval                 # grouping precision/recall
python -m bench.run_all                      # dedupe + throughput + latency
(cd sdk && npm run bench)                     # SDK per-capture overhead

# End-to-end demo over a real socket (API + SDK throwing app)
./scripts/run_demo.sh
```

One-command full stack with Docker:

```bash
docker compose up --build
#   api (:8000)  +  webhook sink (:9000)  +  the `demo` container that throws errors,
#   ships them, and prints the grouped issues. Alerts are delivered to the sink.
open http://localhost:8000/       # minimal dashboard
open http://localhost:8000/docs   # OpenAPI UI
```

## What's measured (2026-08-17)

| Metric | Value | How measured |
|---|---|---|
| Grouping precision / recall | 1.000 / 0.947 (F1 0.973) | crafted labeled set (81 events, 10 groups) |
| False merges / splits | 0 / 1 | 1 deliberate retry-wrapper split (kept on purpose) |
| Homogeneity / completeness | 1.000 / 0.971 | entropy (V-measure 0.985) |
| Dedupe ratio | 416.7x (5,000 -> 12) · demo 7.5x (30 -> 4) | seeded mixed stream · live socket demo |
| Ingest throughput | 716 ev/s (HTTP) · 25,594 ev/s (raw engine) | in-process TestClient · direct `store_event` |
| `/api/store` latency | p50 1.65 ms · p95 1.91 ms | in-process (TestClient), on-disk SQLite |
| `/issues` latency | p50 1.94 ms · p95 2.38 ms | in-process (TestClient) |
| SDK per-capture overhead | p50 0.0099 ms · p95 0.0157 ms (~12 µs) | 20k iters, in-memory transport, 9 frames |
| Tests | 48 pytest (95% cov) + 16 vitest = 64 | pytest + vitest |

Full methodology and reproduce steps in [RESULTS.md](RESULTS.md); raw JSON in `results/`.

## Layout

```
sentinel/                Python package (the pipeline)
  models.py              pydantic event envelope + issue models (boundary validation)
  grouping.py            stack-trace fingerprinting (the core grouping algorithm)
  store.py               SQLite event+issue store, dedupe by (project, fingerprint)
  alerts.py              new-issue + threshold(window) rules -> log / webhook sinks
  config.py              env-driven server config
  api.py                 FastAPI: /api/store, /issues[...], resolve/ignore, /alerts, dashboard
sdk/                     TypeScript SDK (Node ≥20)
  src/stacktrace.ts      V8 Error.stack -> normalized frames (in-app, app-relative)
  src/client.ts          captureException/Message, envelope build, transport, handlers
  src/index.ts           init()/captureException() functional API
  test/                  vitest: stack parsing + client
  demo/demo.ts           throws a mix of errors -> ships to the API -> prints grouped issues
  bench/capture_overhead.ts   per-capture overhead -> results/sdk_overhead.json
eval/                    labeled grouping dataset + precision/recall/homogeneity eval
bench/                   dedupe + throughput + latency ; run_all -> results/*.json
webhook_sink/            tiny FastAPI receiver so compose can show alert deliveries
tests/                   48 pytest tests (grouping/dedupe/ingest/api/alerts/eval)
results/*.json           committed measured numbers (2026-08-17)
docker-compose.yml       api + webhook sink + throwing SDK demo (one command)
```

Stack: Python 3.12, FastAPI + uvicorn, SQLite (WAL), pydantic v2, pytest + coverage; the SDK is TypeScript on Node 20+, built with tsc, tested with vitest. Free/local, CPU-only, no external services or API keys.

## SDK platform support

The SDK is Node-first (v1). Stack parsing (`src/stacktrace.ts`) is environment-agnostic: it parses the V8 `Error.stack` format used by both Node and Chromium browsers, and the envelope shape is identical. Browser wiring is documented but not built in v1. A browser build swaps two Node-only touch points in `src/client.ts` (`node:crypto` `randomBytes` for `crypto.getRandomValues`, and the `process.on("uncaughtException"/"unhandledRejection")` handlers for `window.addEventListener("error"/"unhandledrejection")`) behind the same `SentinelClient` API. The `Transport` interface already uses the global `fetch`, which works in the browser as-is.

## Configuration

| Var | Default | Purpose |
|---|---|---|
| `SENTINEL_DB` | `data/sentinel.sqlite` | SQLite path (`:memory:` supported) |
| `SENTINEL_ALERT_NEW_ISSUE` | `1` | fire an alert on the first sighting of a fingerprint |
| `SENTINEL_ALERT_THRESHOLD` | `10` | fire when an issue's windowed event count crosses N (`0`/`off` disables) |
| `SENTINEL_ALERT_WINDOW` | `60` | threshold window, seconds |
| `SENTINEL_WEBHOOK_URL` | *(unset)* | POST alerts here (unset means log-only) |
| `SENTINEL_URL` / `SENTINEL_PROJECT` | `http://localhost:8000` / `checkout` | SDK demo target |

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/store` | validate + ingest an event envelope -> `{id, issue_id, fingerprint, new_issue, alerts}` |
| `GET /issues` | filter by `project`/`level`/`status`, sort by `count`/`last_seen`/`first_seen` |
| `GET /issues/{id}` | issue + its recent events |
| `POST /issues/{id}/resolve` · `/ignore` · `/unresolve` | change issue status |
| `GET /alerts` | alerts fired so far |
| `GET /health` · `GET /` | event/issue counts · minimal HTML dashboard |

```bash
# same root cause, different line numbers + user data -> ONE issue, count increments:
curl -s -X POST localhost:8000/api/store -H 'content-type: application/json' -d '{
  "event_id":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa","timestamp":1723000000,"project":"web",
  "exception":{"values":[{"type":"TypeError","value":"Cannot read properties of undefined (reading name) user 12345",
   "stacktrace":{"frames":[{"function":"renderUserCard","filename":"src/UserCard.js","lineno":48,"in_app":true}]}}]}}'

curl -s "localhost:8000/issues?sort=count&order=desc" | python3 -m json.tool
```

## Limitations

- All data is synthetic and seeded (grouping dataset plus a mixed stream generator); there is no real production error stream behind these numbers.
- Grouping precision/recall is measured on a hand-crafted labeled dataset. It deliberately contains one false split (a retry-wrapper frame that shifts the signature) so the recall number reflects a real weakness of frame-signature grouping instead of being a curated 1.0.
- Latency and throughput are measured in-process (FastAPI `TestClient`), not over a network socket. The socket path is proven working by the end-to-end demo, just not timed.
- The store is SQLite, a deliberate choice to keep the project local, zero-dependency, and byte-reproducible. All SQL is isolated in `sentinel/store.py`, so a Postgres swap would touch only that file.
- Frame-signature grouping is sensitive to inserted/entry frames; production monitors layer additional stack-trace normalization rules on top of this idea for exactly that reason.
- Not built: source-map symbolication for minified JS, per-project rate limiting/quotas, spike-detection alerts, and any production UI framework.
