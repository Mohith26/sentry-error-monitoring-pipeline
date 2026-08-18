# Résumé Bullets — Sentinel (filled strictly from measured results)

> Measured 2026-08-17. Every number traces to `results/*.json` / RESULTS.md.
> Unmeasured values would be the literal `___`; there are none — all three bullets are
> fully filled. Honesty tags below each bullet and in the ledger.

## Filled bullets

- Built a **Sentry-style error-monitoring pipeline** (TypeScript SDK → Python/FastAPI
  ingest) that **fingerprints normalized stack traces to group errors into issues at
  1.000 precision / 0.947 recall** vs a labeled ground-truth set (0 false merges, 1
  false split), collapsing a mixed stream of **5,000 raw events → 12 issues (416.7×
  dedupe)** — and **30 live thrown errors → 4 issues (7.5×)** in the end-to-end demo.
  <br>_(MEASURED: precision/recall pairwise over 81-event crafted labeled set, 10 true
  groups; homogeneity 1.000 / completeness 0.971. **Honesty:** dataset is
  **synthetic/crafted** and contains **1 deliberate false split** (retry-wrapper frame)
  so recall is honest, not curated.)_

- Ingested **716 events/sec** end-to-end (**25,594 events/sec** through the grouping/store
  engine core) with **`/api/store` p95 1.91 ms** and served issue queries + alerts
  (filter/sort, resolve/ignore, **new-issue + threshold→webhook** alerts) at **`/issues`
  p95 2.38 ms** over a **SQLite-backed** issue store.
  <br>_(MEASURED: throughput over 5,000 events; latency over 2,000 requests each, 100
  warm-up excluded. **Honesty:** throughput + latency measured **in-process via FastAPI
  `TestClient`, NOT over a network socket**; the socket path is proven functional by the
  demo but not timed. Store is **SQLite**, not Postgres — corrected from the template.)_

- Wrote the **JS/TS SDK** (Node ≥20) capturing **uncaught exceptions** + `captureException`
  with **normalized stack traces** (function/file/lineno/colno, in-app rules, app-relative
  paths) + context (release/env/tags/breadcrumbs) at **~0.012 ms/capture (p95 0.0157 ms)**
  overhead, verified by **64 passing tests** (48 pytest + 16 vitest) across grouping,
  dedupe, ingest, alerts, and SDK stack-parsing.
  <br>_(MEASURED: overhead = 20,000 iters, 9 frames/event, in-memory transport (no
  network), Node v22.23.1; p50 0.0099 ms / p95 0.0157 ms. Test counts from `pytest -q`
  and `vitest run`; Python coverage 95% on `sentinel/`.)_

## Measured-value ledger

| Placeholder | Value | Status |
|---|---|---|
| grouping precision / recall | 1.000 / 0.947 (F1 0.973) | MEASURED (synthetic/crafted set) |
| false merges / splits | 0 / 1 | MEASURED (1 deliberate split) |
| homogeneity / completeness / V | 1.000 / 0.971 / 0.985 | MEASURED |
| dedupe ratio (mixed stream) | 5,000 → 12 = 416.7× | MEASURED (seeded, in-process) |
| dedupe ratio (live demo) | 30 → 4 = 7.5× | MEASURED (real socket) |
| throughput — HTTP ingest | 716 ev/s | MEASURED (in-process TestClient) |
| throughput — raw engine | 25,594 ev/s | MEASURED (direct store_event) |
| `/api/store` p50 / p95 | 1.65 / 1.91 ms | MEASURED (in-process) |
| `/issues` p50 / p95 | 1.94 / 2.38 ms | MEASURED (in-process) |
| SDK per-capture p50 / p95 | 0.0099 / 0.0157 ms (~12 µs) | MEASURED (in-memory transport) |
| tests (pytest / vitest / total) | 48 / 16 / 64 | MEASURED |
| Python coverage on `sentinel/` | 95% | MEASURED |

## Honesty tags
- ✅ MEASURED from real runs; synthetic seeded data throughout.
- ⚠️ Grouping precision/recall on a **synthetic/crafted** labeled dataset with **1
  intentional false split** (retry-wrapper frame) — recall 0.947 is honest, not curated;
  false merges = 0.
- ⚠️ Throughput + latency measured **in-process (FastAPI TestClient / ASGI)**, excluding
  the network socket. HTTP throughput is machine-load-sensitive (a busier run measured
  ~286 ev/s); the raw-engine figure is the stable core measure.
- ⚠️ Store is **SQLite** (documented choice), not Postgres — the résumé-template word was
  corrected to match what was actually built.
- ⚠️ SDK overhead excludes network transport (in-memory transport).
- ❌ Not the real Sentry SDK/Relay/ClickHouse; no source-map symbolication; no cloud deploy.
