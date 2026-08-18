"""Issue store + dedupe behaviour."""

from __future__ import annotations

import time

from sentinel.models import EventEnvelope

from .conftest import frame, make_envelope


def _store(store, env: dict):
    return store.store_event(EventEnvelope(**env))


def test_same_fingerprint_rolls_into_one_issue(store):
    base_frames = [frame("render", "src/a.js", lineno=10)]
    r1 = _store(store, make_envelope(event_id="1" * 32, frames=base_frames))
    r2 = _store(
        store,
        make_envelope(event_id="2" * 32, frames=[frame("render", "src/a.js", lineno=55)]),
    )
    assert r1.new_issue is True
    assert r2.new_issue is False
    assert r1.issue_id == r2.issue_id
    issue = store.get_issue(r1.issue_id)
    assert issue.count == 2
    events, issues = store.counts()
    assert events == 2 and issues == 1


def test_distinct_causes_stay_separate(store):
    r1 = _store(store, make_envelope(event_id="1" * 32, frames=[frame("a", "src/a.js")]))
    r2 = _store(store, make_envelope(event_id="2" * 32, frames=[frame("b", "src/b.js")]))
    assert r1.issue_id != r2.issue_id
    _, issues = store.counts()
    assert issues == 2


def test_first_and_last_seen_track(store):
    _store(store, make_envelope(event_id="1" * 32, frames=[frame("a", "src/a.js")],
                                timestamp=1000.0))
    r = _store(store, make_envelope(event_id="2" * 32, frames=[frame("a", "src/a.js")],
                                    timestamp=2000.0))
    issue = store.get_issue(r.issue_id)
    assert issue.first_seen == 1000.0
    assert issue.last_seen == 2000.0


def test_count_increments_are_exact(store):
    for i in range(25):
        _store(store, make_envelope(event_id=f"{i:032x}", frames=[frame("a", "src/a.js")]))
    _, issues = store.counts()
    assert issues == 1
    issue = store.list_issues()[0]
    assert issue.count == 25


def test_dedupe_ratio_on_mixed_stream(store):
    # 5 true causes, 60 events distributed unevenly -> 5 issues
    causes = [
        [frame("f1", "src/one.js")],
        [frame("f2", "src/two.js")],
        [frame("f3", "src/three.js")],
        [frame("f4", "src/four.js")],
        [frame("f5", "src/five.js")],
    ]
    weights = [20, 15, 12, 8, 5]  # sum 60
    n = 0
    for idx, w in enumerate(weights):
        for _ in range(w):
            _store(store, make_envelope(event_id=f"{n:032x}",
                                        frames=[dict(causes[idx][0], lineno=n % 90 + 1)]))
            n += 1
    events, issues = store.counts()
    assert events == 60
    assert issues == 5
    assert round(events / issues, 2) == 12.0


def test_project_scoping_isolates_fingerprints(store):
    r1 = _store(store, make_envelope(event_id="1" * 32, project="a",
                                     frames=[frame("f", "src/x.js")]))
    r2 = _store(store, make_envelope(event_id="2" * 32, project="b",
                                     frames=[frame("f", "src/x.js")]))
    # same fingerprint hash, different project -> different issues
    assert r1.fingerprint == r2.fingerprint
    assert r1.issue_id != r2.issue_id


def test_duplicate_event_id_is_idempotent_on_events(store):
    _store(store, make_envelope(event_id="1" * 32, frames=[frame("a", "src/a.js")]))
    # same event id again: INSERT OR IGNORE keeps events table clean
    _store(store, make_envelope(event_id="1" * 32, frames=[frame("a", "src/a.js")]))
    events, _ = store.counts()
    assert events == 1
