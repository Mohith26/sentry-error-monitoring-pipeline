# Sentinel — Error-Monitoring Ingest + Stack-Trace Grouping Pipeline (mini-Sentry)

A miniature **error-monitoring pipeline in Sentry's own shape**: a **TypeScript SDK**
captures uncaught exceptions + `captureException` with a normalized **stack trace** and
context, and POSTs an event envelope to a **Python/FastAPI ingest API**, which
**fingerprints the stack trace to group same-root-cause events into one Issue** (dedupe),
stores events, and exposes a **query + alerting API** with a minimal dashboard.
Benchmarked for **grouping precision/recall, dedupe ratio, ingest throughput, and query
latency**.

> Built for a **Sentry — Software Engineer Intern** target (error monitoring /
> observability; production **Python + JS/TS**). It rebuilds Sentry's single most
> important algorithm — **issue grouping**: turning a flood of raw error events into a
> deduplicated set of actionable issues by fingerprinting the stack trace (normalizing
> frames, collapsing the volatile parts).

> ### Data & measurement notes (read me)
> - **100% synthetic, seeded** data (grouping dataset + mixed stream). No real records.
> - **Grouping precision/recall is measured on a hand-crafted labeled dataset** that
>   contains **1 deliberate false split** so the recall number is honest, not curated.
> - **Latency/throughput are measured in-process** (FastAPI `TestClient`), **not over a
>   network socket** — the socket path is proven by the end-to-end demo but not timed.
> - Full methodology + every measured number: **[RESULTS.md](RESULTS.md)**; raw JSON in
>   `results/`; résumé bullets in **[BULLETS.md](BULLETS.md)**.

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

**Grouping (the core), in `sentinel/grouping.py`:** fingerprint the **frame signature**
(per frame: normalized module + function), preferring **in-app** frames, with
**line/column numbers excluded** so "same stack, different build" groups. Fall back to
exception type + normalized message, then to the message. Message normalization strips the
volatile parts — memory addresses, hex, UUIDs, integers (incl. `2000ms`), quoted user data,
emails — so `User 12345 not found` and `User 67890 not found` collapse. Deterministic
(SHA-1). In-app frame rules are configurable.

## Tech stack

Python 3.12 · **FastAPI** + uvicorn · **SQLite** (WAL; documented choice — local,
zero external services, all SQL isolated in `store.py`) · pydantic v2 · pytest + coverage.
**TypeScript SDK** on Node ≥20, built with **tsc**, tested with **vitest**. Docker +
docker-compose. Free/local, CPU-only, no external services or API keys.

## Layout

```
sentinel/                Python package (the pipeline)
  models.py              pydantic event envelope + issue models (boundary validation)
  grouping.py            stack-trace fingerprinting — the core grouping algorithm
  store.py               SQLite event+issue store, dedupe by (project, fingerprint)
  alerts.py              new-issue + threshold(window) rules -> log / webhook sinks
  config.py              env-driven server config
  api.py                 FastAPI: /api/store, /issues[...], resolve/ignore, /alerts, dashboard
sdk/                     TypeScript SDK (Node ≥20)
  src/stacktrace.ts      V8 Error.stack -> normalized frames (in-app, app-relative)
  src/client.ts          captureException/Message, envelope build, transport, handlers
  src/index.ts           Sentry-style init()/captureException() functional API
  test/                  vitest: stack parsing + client
  demo/demo.ts           throws a mix of errors -> ships to the API -> prints grouped issues
  bench/capture_overhead.ts   per-capture overhead -> results/sdk_overhead.json
eval/                    labeled grouping dataset + precision/recall/homogeneity eval
bench/                   dedupe + throughput + latency ; run_all -> results/*.json
webhook_sink/            tiny FastAPI receiver so compose can show alert deliveries
tests/                   48 pytest tests (grouping/dedupe/ingest/api/alerts/eval)
results/*.json           committed measured numbers (2026-08-17)
docker-compose.yml       api + webhook sink + throwing SDK demo (one command)
RESULTS.md / BULLETS.md / STATUS.json
```

## Quickstart

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

### One-command full stack (Docker)

```bash
docker compose up --build
#   api (:8000)  +  webhook sink (:9000)  +  the `demo` container that throws errors,
#   ships them, and prints the grouped issues. Alerts are delivered to the sink.
open http://localhost:8000/       # minimal dashboard
open http://localhost:8000/docs   # OpenAPI UI
```

### Configuration (env)

| Var | Default | Purpose |
|---|---|---|
| `SENTINEL_DB` | `data/sentinel.sqlite` | SQLite path (`:memory:` supported) |
| `SENTINEL_ALERT_NEW_ISSUE` | `1` | fire an alert on the first sighting of a fingerprint |
| `SENTINEL_ALERT_THRESHOLD` | `10` | fire when an issue's windowed event count crosses N (`0`/`off` disables) |
| `SENTINEL_ALERT_WINDOW` | `60` | threshold window, seconds |
| `SENTINEL_WEBHOOK_URL` | *(unset)* | POST alerts here (unset → log-only) |
| `SENTINEL_URL` / `SENTINEL_PROJECT` | `http://localhost:8000` / `checkout` | SDK demo target |

## API

| Endpoint | Purpose |
|---|---|
| `POST /api/store` | validate + ingest an event envelope → `{id, issue_id, fingerprint, new_issue, alerts}` |
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

## Measured results (2026-08-17)

| Metric | Value | How measured |
|---|---|---|
| Grouping precision / recall | **1.000 / 0.947** (F1 0.973) | crafted labeled set (81 events, 10 groups) |
| False merges / splits | **0 / 1** | 1 deliberate retry-wrapper split (honest) |
| Homogeneity / completeness | **1.000 / 0.971** | entropy (V-measure 0.985) |
| Dedupe ratio | **416.7×** (5,000 → 12) · demo **7.5×** (30 → 4) | seeded mixed stream · live socket demo |
| Ingest throughput | **716 ev/s** (HTTP) · **25,594 ev/s** (raw engine) | in-process TestClient · direct `store_event` |
| `/api/store` latency | p50 **1.65 ms** · p95 **1.91 ms** | in-process (TestClient), on-disk SQLite |
| `/issues` latency | p50 **1.94 ms** · p95 **2.38 ms** | in-process (TestClient) |
| SDK per-capture overhead | p50 **0.0099 ms** · p95 **0.0157 ms** (~12 µs) | 20k iters, in-memory transport, 9 frames |
| Tests | **48 pytest** (95% cov) + **16 vitest** = **64** | — |

Full detail, honesty tags, and exact reproduce steps in **[RESULTS.md](RESULTS.md)**.
