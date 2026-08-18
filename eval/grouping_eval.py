"""Evaluate grouping quality against the labeled dataset.

Reports (all from a real run over the crafted dataset):
  - pairwise precision / recall / F1  (over all event pairs)
  - false merges  (predicted clusters mixing >1 true group)
  - false splits  (true groups spread across >1 predicted cluster)
  - homogeneity / completeness / V-measure (entropy-based, sklearn formulas)
  - dedupe: events -> predicted clusters
"""

from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from itertools import combinations
from typing import Dict, List

from sentinel.grouping import compute_fingerprint
from .dataset import build_dataset

RESULTS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "results")


def _pairwise(true_labels: List[str], pred_labels: List[str]) -> Dict[str, float]:
    tp = fp = fn = tn = 0
    for i, j in combinations(range(len(true_labels)), 2):
        same_true = true_labels[i] == true_labels[j]
        same_pred = pred_labels[i] == pred_labels[j]
        if same_true and same_pred:
            tp += 1
        elif not same_true and same_pred:
            fp += 1
        elif same_true and not same_pred:
            fn += 1
        else:
            tn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
    }


def _false_merges_splits(true_labels, pred_labels):
    pred_to_true = defaultdict(set)
    true_to_pred = defaultdict(set)
    for t, p in zip(true_labels, pred_labels):
        pred_to_true[p].add(t)
        true_to_pred[t].add(p)
    false_merges = sum(1 for ts in pred_to_true.values() if len(ts) > 1)
    false_splits = sum(1 for ps in true_to_pred.values() if len(ps) > 1)
    return false_merges, false_splits, pred_to_true, true_to_pred


def _entropy(counts):
    total = sum(counts)
    if total == 0:
        return 0.0
    return -sum((c / total) * math.log(c / total) for c in counts if c > 0)


def _homogeneity_completeness(true_labels, pred_labels):
    n = len(true_labels)
    classes = Counter(true_labels)
    clusters = Counter(pred_labels)
    h_c = _entropy(list(classes.values()))
    h_k = _entropy(list(clusters.values()))
    # H(C|K)
    joint = Counter(zip(true_labels, pred_labels))
    h_c_given_k = 0.0
    for k, k_count in clusters.items():
        for c in classes:
            n_ck = joint.get((c, k), 0)
            if n_ck > 0:
                h_c_given_k -= (n_ck / n) * math.log(n_ck / k_count)
    h_k_given_c = 0.0
    for c, c_count in classes.items():
        for k in clusters:
            n_ck = joint.get((c, k), 0)
            if n_ck > 0:
                h_k_given_c -= (n_ck / n) * math.log(n_ck / c_count)
    homogeneity = 1.0 if h_c == 0 else 1 - h_c_given_k / h_c
    completeness = 1.0 if h_k == 0 else 1 - h_k_given_c / h_k
    v = (
        0.0
        if (homogeneity + completeness) == 0
        else 2 * homogeneity * completeness / (homogeneity + completeness)
    )
    return homogeneity, completeness, v


def run_eval() -> dict:
    data = build_dataset()
    true_labels = [d.true_group for d in data]
    pred_labels = [compute_fingerprint(d.event).hash for d in data]

    pw = _pairwise(true_labels, pred_labels)
    fm, fs, pred_to_true, true_to_pred = _false_merges_splits(true_labels, pred_labels)
    homo, comp, v = _homogeneity_completeness(true_labels, pred_labels)

    n_true = len(set(true_labels))
    n_pred = len(set(pred_labels))

    result = {
        "dataset": "synthetic/crafted labeled grouping dataset (eval/dataset.py, seed=1337)",
        "events": len(data),
        "true_groups": n_true,
        "predicted_clusters": n_pred,
        "precision": round(pw["precision"], 4),
        "recall": round(pw["recall"], 4),
        "f1": round(pw["f1"], 4),
        "pairwise": {k: pw[k] for k in ("tp", "fp", "fn", "tn")},
        "false_merges": fm,
        "false_splits": fs,
        "homogeneity": round(homo, 4),
        "completeness": round(comp, 4),
        "v_measure": round(v, 4),
        "dedupe_ratio": round(len(data) / n_pred, 4),
    }
    # which true groups got split, for transparency in RESULTS.md
    result["split_groups"] = sorted(t for t, ps in true_to_pred.items() if len(ps) > 1)
    result["merged_clusters"] = [
        sorted(list(ts)) for ts in pred_to_true.values() if len(ts) > 1
    ]
    return result


def main():
    os.makedirs(RESULTS_DIR, exist_ok=True)
    result = run_eval()
    path = os.path.join(RESULTS_DIR, "grouping_eval.json")
    with open(path, "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps(result, indent=2))
    print(f"\nwrote {path}")


if __name__ == "__main__":
    main()
