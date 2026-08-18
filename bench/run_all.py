"""Run every benchmark + the grouping eval and write results/*.json + summary.json."""

from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
import time

from eval.grouping_eval import run_eval

from . import dedupe, latency, throughput

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results")


def _write(name: str, data: dict) -> None:
    os.makedirs(RESULTS_DIR, exist_ok=True)
    with open(os.path.join(RESULTS_DIR, name), "w") as f:
        json.dump(data, f, indent=2)
    print(f"wrote results/{name}")


def _sdk_overhead() -> dict | None:
    """Load the SDK capture-overhead result if the SDK bench has been run."""
    path = os.path.join(RESULTS_DIR, "sdk_overhead.json")
    if os.path.exists(path):
        with open(path) as f:
            return json.load(f)
    return None


def main() -> None:
    print("== grouping eval ==")
    grouping = run_eval()
    _write("grouping_eval.json", grouping)

    print("== dedupe ==")
    dd = dedupe.run()
    _write("dedupe.json", dd)

    print("== throughput ==")
    tp = throughput.run()
    _write("throughput.json", tp)

    print("== latency ==")
    lat = latency.run()
    _write("latency.json", lat)

    summary = {
        "measured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "grouping": {
            "events": grouping["events"],
            "true_groups": grouping["true_groups"],
            "predicted_clusters": grouping["predicted_clusters"],
            "precision": grouping["precision"],
            "recall": grouping["recall"],
            "f1": grouping["f1"],
            "false_merges": grouping["false_merges"],
            "false_splits": grouping["false_splits"],
            "homogeneity": grouping["homogeneity"],
            "completeness": grouping["completeness"],
        },
        "dedupe": {
            "raw_events": dd["raw_events"],
            "resulting_issues": dd["resulting_issues"],
            "dedupe_ratio": dd["dedupe_ratio"],
        },
        "throughput_http_events_per_sec": tp["http_path"]["events_per_sec"],
        "throughput_raw_engine_events_per_sec": tp["raw_engine_path"]["events_per_sec"],
        "store_p50_ms": lat["store"]["p50_ms"],
        "store_p95_ms": lat["store"]["p95_ms"],
        "issues_p50_ms": lat["issues_query"]["p50_ms"],
        "issues_p95_ms": lat["issues_query"]["p95_ms"],
        "sdk_overhead": _sdk_overhead(),
    }
    _write("summary.json", summary)
    print("\n== SUMMARY ==")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
