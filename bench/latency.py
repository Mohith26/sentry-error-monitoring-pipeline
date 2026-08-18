"""Latency: /api/store and /issues p50/p95/p99 (in-process TestClient).

Same in-process caveat as throughput: measured over the ASGI app, not a real socket.
Per-request wall time via time.perf_counter around each client call.
"""

from __future__ import annotations

import os
import tempfile
import time

from fastapi.testclient import TestClient

from sentinel.alerts import AlertConfig, AlertManager
from sentinel.api import create_app
from sentinel.store import Store

from .util import mixed_stream, summarize

N_STORE = 2000
N_QUERY = 2000
WARMUP = 100


def run(n_store: int = N_STORE, n_query: int = N_QUERY) -> dict:
    tmp = tempfile.mkdtemp(prefix="sentinel_lat_")
    store = Store(os.path.join(tmp, "latency.sqlite"))
    alerts = AlertManager(AlertConfig(new_issue=True, threshold=100, window_seconds=60))
    client = TestClient(create_app(store, alerts))

    events = [e.model_dump() for e in mixed_stream(n_store + WARMUP)]

    # ---- /api/store latency ----
    store_ms = []
    for i, e in enumerate(events):
        t0 = time.perf_counter()
        client.post("/api/store", json=e)
        dt = (time.perf_counter() - t0) * 1000.0
        if i >= WARMUP:
            store_ms.append(dt)

    # ---- /issues latency (query a populated store) ----
    query_ms = []
    sorts = ["count", "last_seen"]
    for i in range(n_query + WARMUP):
        params = {"sort": sorts[i % 2], "order": "desc", "limit": 100}
        t0 = time.perf_counter()
        client.get("/issues", params=params)
        dt = (time.perf_counter() - t0) * 1000.0
        if i >= WARMUP:
            query_ms.append(dt)

    total_events, total_issues = store.counts()
    store.close()

    return {
        "measurement": "in-process TestClient (ASGI), on-disk SQLite (WAL), excludes network socket",
        "warmup_excluded": WARMUP,
        "store_events": total_events,
        "store_issues": total_issues,
        "store": summarize(store_ms),
        "issues_query": summarize(query_ms),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
