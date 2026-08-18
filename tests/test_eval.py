"""Guards on the grouping eval so the reported numbers stay honest + reproducible."""

from __future__ import annotations

from eval.dataset import build_dataset
from eval.grouping_eval import run_eval


def test_dataset_is_deterministic():
    a = build_dataset()
    b = build_dataset()
    assert [x.true_group for x in a] == [y.true_group for y in b]
    assert [x.event.event_id for x in a] == [y.event.event_id for y in b]


def test_dataset_shape():
    data = build_dataset()
    assert len(data) == 81
    assert len(set(d.true_group for d in data)) == 10


def test_eval_precision_perfect_no_false_merges():
    r = run_eval()
    # distinct causes never merge -> precision 1.0, zero false merges
    assert r["precision"] == 1.0
    assert r["false_merges"] == 0


def test_eval_recall_high():
    r = run_eval()
    assert r["recall"] >= 0.90


def test_eval_only_known_false_split():
    r = run_eval()
    # the single intended limitation: the retry-wrapper group splits on the extra frame
    assert r["false_splits"] == 1
    assert r["split_groups"] == ["G10_webhook_signature"]


def test_eval_homogeneity_perfect():
    r = run_eval()
    assert r["homogeneity"] == 1.0
