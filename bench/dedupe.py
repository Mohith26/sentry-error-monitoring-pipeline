"""Dedupe ratio on a realistic mixed stream: raw events / resulting issues."""

from __future__ import annotations

from sentinel.store import Store

from .util import mixed_stream

N_EVENTS = 5000
N_CAUSES = 12


def run(n: int = N_EVENTS, n_causes: int = N_CAUSES) -> dict:
    store = Store(":memory:")
    events = mixed_stream(n, n_causes=n_causes)
    for e in events:
        store.store_event(e)
    total_events, total_issues = store.counts()
    store.close()
    return {
        "measurement": "in-process store_event over a seeded mixed stream",
        "root_causes_in_stream": n_causes,
        "raw_events": total_events,
        "resulting_issues": total_issues,
        "dedupe_ratio": round(total_events / total_issues, 2),
    }


if __name__ == "__main__":
    import json

    print(json.dumps(run(), indent=2))
