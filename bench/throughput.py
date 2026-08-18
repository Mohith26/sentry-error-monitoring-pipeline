"""Ingest throughput: events/sec through POST /api/store (in-process TestClient).

Measured in-process via FastAPI's TestClient (ASGI) — it exercises the full ingest
path (pydantic validation -> fingerprint -> SQLite dedupe -> alert evaluation) but
excludes the HTTP/TCP socket. On-disk SQLite (temp file, WAL), not :memory:.
"""

from __future__ import annotations

import os
import tempfile
import time

from fastapi.testclient import TestClient

from sentinel.alerts import AlertConfig, AlertManager
from sentinel.api import create_app
from sentinel.store import Store

from .util import mixed_stream

N_EVENTS = 5000


def run(n: int = N_EVENTS) -> dict:
    tmp = tempfile.mkdtemp(prefix="sentinel_bench_")
    models = mixed_stream(n)
    payloads = [e.model_dump() for e in models]

    # ---- (1) full HTTP ingest path: POST /api/store via TestClient ----
    http_store = Store(os.path.join(tmp, "http.sqlite"))
    http_alerts = AlertManager(AlertConfig(new_issue=True, threshold=100, window_seconds=60))
    client = TestClient(create_app(http_store, http_alerts))
    for e in payloads[:50]:  # warmup
        client.post("/api/store", json=e)
    start = time.perf_counter()
    for e in payloads:
        client.post("/api/store", json=e)
    http_elapsed = time.perf_counter() - start
    http_events, http_issues = http_store.counts()
    http_store.close()

    # ---- (2) raw engine path: store_event (validation+fingerprint+dedupe+SQLite) ----
    raw_store = Store(os.path.join(tmp, "raw.sqlite"))
    for e in models[:50]:  # warmup
        raw_store.store_event(e)
    raw_store2 = Store(os.path.join(tmp, "raw2.sqlite"))
    start = time.perf_counter()
    for e in models:
        raw_store2.store_event(e)
    raw_elapsed = time.perf_counter() - start
    raw_events, raw_issues = raw_store2.counts()
    raw_store.close()
    raw_store2.close()

    return {
        "events_posted": n,
        "http_path": {
            "measurement": "in-process TestClient (ASGI) POST /api/store, on-disk SQLite (WAL), "
            "excludes network socket; includes JSON parse + pydantic validation + grouping + dedupe + alert eval",
            "elapsed_s": round(http_elapsed, 4),
            "events_per_sec": round(n / http_elapsed, 1),
            "stored_events": http_events,
            "resulting_issues": http_issues,
        },
        "raw_engine_path": {
            "measurement": "direct store_event() over pre-built envelopes (fingerprint + dedupe + "
            "SQLite on-disk WAL); no HTTP/ASGI, no JSON parse",
            "elapsed_s": round(raw_elapsed, 4),
            "events_per_sec": round(n / raw_elapsed, 1),
            "stored_events": raw_events,
            "resulting_issues": raw_issues,
        },
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
