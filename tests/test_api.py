"""Ingest + query API integration tests (in-process TestClient)."""

from __future__ import annotations

from .conftest import frame, make_envelope


# ---- ingest validation ----------------------------------------------------
def test_store_accepts_valid_envelope(client):
    resp = client.post("/api/store", json=make_envelope(frames=[frame("a", "src/a.js")]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["new_issue"] is True
    assert len(body["fingerprint"]) == 40  # sha1 hex


def test_store_rejects_bad_event_id(client):
    bad = make_envelope(frames=[frame("a", "src/a.js")])
    bad["event_id"] = "not-hex"
    assert client.post("/api/store", json=bad).status_code == 422


def test_store_rejects_missing_payload(client):
    bad = make_envelope(frames=[frame("a", "src/a.js")])
    del bad["exception"]  # no exception and no message
    assert client.post("/api/store", json=bad).status_code == 422


def test_store_rejects_bad_level(client):
    bad = make_envelope(frames=[frame("a", "src/a.js")], level="explode")
    assert client.post("/api/store", json=bad).status_code == 422


def test_store_rejects_non_positive_timestamp(client):
    bad = make_envelope(frames=[frame("a", "src/a.js")])
    bad["timestamp"] = 0
    assert client.post("/api/store", json=bad).status_code == 422


# ---- query filter / sort --------------------------------------------------
def _seed(client):
    client.post("/api/store", json=make_envelope(
        event_id="1" * 32, project="web", level="error", frames=[frame("a", "src/a.js")]))
    for i in range(3):  # this fingerprint gets 3 events
        client.post("/api/store", json=make_envelope(
            event_id=f"{i:032x}".replace("0", "2", 1) + "", project="web", level="warning",
            frames=[frame("b", "src/b.js", lineno=i)]))
    client.post("/api/store", json=make_envelope(
        event_id="9" * 32, project="api", level="error", frames=[frame("c", "src/c.js")]))


def test_issues_filter_by_project(client):
    _seed(client)
    web = client.get("/issues", params={"project": "web"}).json()
    api = client.get("/issues", params={"project": "api"}).json()
    assert all(i["project"] == "web" for i in web["issues"])
    assert all(i["project"] == "api" for i in api["issues"])
    assert web["count"] >= 2 and api["count"] == 1


def test_issues_filter_by_level(client):
    _seed(client)
    warnings = client.get("/issues", params={"level": "warning"}).json()["issues"]
    assert warnings and all(i["level"] == "warning" for i in warnings)


def test_issues_sort_by_count_desc(client):
    _seed(client)
    issues = client.get("/issues", params={"sort": "count", "order": "desc"}).json()["issues"]
    counts = [i["count"] for i in issues]
    assert counts == sorted(counts, reverse=True)
    assert counts[0] >= 3  # the b/src/b.js issue got 3 events


def test_issue_detail_returns_events(client):
    r = client.post("/api/store", json=make_envelope(frames=[frame("a", "src/a.js")])).json()
    detail = client.get(f"/issues/{r['issue_id']}").json()
    assert detail["issue"]["id"] == r["issue_id"]
    assert detail["event_count"] == 1


def test_issue_detail_404(client):
    assert client.get("/issues/99999").status_code == 404


# ---- state changes --------------------------------------------------------
def test_resolve_and_ignore(client):
    r = client.post("/api/store", json=make_envelope(frames=[frame("a", "src/a.js")])).json()
    iid = r["issue_id"]
    assert client.post(f"/issues/{iid}/resolve").json()["issue"]["status"] == "resolved"
    assert client.post(f"/issues/{iid}/ignore").json()["issue"]["status"] == "ignored"
    assert client.post(f"/issues/{iid}/unresolve").json()["issue"]["status"] == "unresolved"
    # filter by status
    client.post(f"/issues/{iid}/resolve")
    resolved = client.get("/issues", params={"status": "resolved"}).json()["issues"]
    assert any(i["id"] == iid for i in resolved)


def test_resolve_missing_issue_404(client):
    assert client.post("/issues/12345/resolve").status_code == 404


# ---- dashboard + health ---------------------------------------------------
def test_dashboard_renders(client):
    client.post("/api/store", json=make_envelope(frames=[frame("a", "src/a.js")]))
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Sentinel" in resp.text
    assert "grouped issues" in resp.text


def test_health(client):
    client.post("/api/store", json=make_envelope(frames=[frame("a", "src/a.js")]))
    h = client.get("/health").json()
    assert h["status"] == "ok"
    assert h["events"] == 1 and h["issues"] == 1
