"""FastAPI app: ingest (/api/store), query (/issues), state changes, alerts, dashboard.

The app is built by `create_app(store, alert_manager)` so tests inject a fresh
in-memory store; `app` at the bottom is the production instance from env config.
"""

from __future__ import annotations

import html
import time
from typing import Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse

from .alerts import AlertConfig, AlertManager
from .config import load_config
from .models import EventEnvelope, Issue, VALID_STATUSES
from .store import Store


def create_app(store: Store, alert_manager: AlertManager) -> FastAPI:
    app = FastAPI(title="Sentinel", version="1.0.0", description="mini-Sentry ingest + grouping")
    app.state.store = store
    app.state.alerts = alert_manager

    def get_store() -> Store:
        return app.state.store

    def get_alerts() -> AlertManager:
        return app.state.alerts

    # ---- ingest --------------------------------------------------------
    @app.post("/api/store")
    def store_event(
        event: EventEnvelope,
        store: Store = Depends(get_store),
        alerts: AlertManager = Depends(get_alerts),
    ):
        result = store.store_event(event)
        issue = store.get_issue(result.issue_id)
        now = time.time()
        window_count = store.window_event_count(
            result.issue_id, alerts.config.window_seconds, now
        )
        fired = alerts.evaluate(issue, result.new_issue, window_count)
        return {
            "id": result.id,
            "issue_id": result.issue_id,
            "fingerprint": result.fingerprint,
            "new_issue": result.new_issue,
            "alerts": [a.rule for a in fired],
        }

    # ---- query ---------------------------------------------------------
    @app.get("/issues")
    def list_issues(
        project: Optional[str] = None,
        level: Optional[str] = None,
        status: Optional[str] = None,
        sort: str = Query("last_seen", pattern="^(count|last_seen|first_seen)$"),
        order: str = Query("desc", pattern="^(asc|desc)$"),
        limit: int = Query(100, ge=1, le=1000),
        offset: int = Query(0, ge=0),
        store: Store = Depends(get_store),
    ):
        issues = store.list_issues(
            project=project, level=level, status=status,
            sort=sort, order=order, limit=limit, offset=offset,
        )
        return {"issues": [i.model_dump() for i in issues], "count": len(issues)}

    @app.get("/issues/{issue_id}")
    def get_issue(issue_id: int, store: Store = Depends(get_store)):
        issue = store.get_issue(issue_id)
        if issue is None:
            raise HTTPException(status_code=404, detail="issue not found")
        events = store.get_events_for_issue(issue_id, limit=50)
        return {"issue": issue.model_dump(), "events": events, "event_count": len(events)}

    def _set_status(issue_id: int, status: str, store: Store) -> dict:
        if status not in VALID_STATUSES:
            raise HTTPException(status_code=400, detail="invalid status")
        issue = store.get_issue(issue_id)
        if issue is None:
            raise HTTPException(status_code=404, detail="issue not found")
        updated = store.set_status(issue_id, status)
        return {"issue": updated.model_dump()}

    @app.post("/issues/{issue_id}/resolve")
    def resolve(issue_id: int, store: Store = Depends(get_store)):
        return _set_status(issue_id, "resolved", store)

    @app.post("/issues/{issue_id}/ignore")
    def ignore(issue_id: int, store: Store = Depends(get_store)):
        return _set_status(issue_id, "ignored", store)

    @app.post("/issues/{issue_id}/unresolve")
    def unresolve(issue_id: int, store: Store = Depends(get_store)):
        return _set_status(issue_id, "unresolved", store)

    # ---- alerts + health ----------------------------------------------
    @app.get("/alerts")
    def recent_alerts(alerts: AlertManager = Depends(get_alerts)):
        return {"alerts": [a.to_dict() for a in alerts.fired]}

    @app.get("/health")
    def health(store: Store = Depends(get_store)):
        events, issues = store.counts()
        return {"status": "ok", "events": events, "issues": issues}

    # ---- minimal dashboard --------------------------------------------
    @app.get("/", response_class=HTMLResponse)
    def dashboard(store: Store = Depends(get_store)):
        issues = store.list_issues(sort="last_seen", order="desc", limit=100)
        return HTMLResponse(_render_dashboard(issues))

    return app


def _render_dashboard(issues) -> str:
    rows = []
    for i in issues:
        badge = {
            "unresolved": "#e03e3e",
            "resolved": "#2a9d5c",
            "ignored": "#888",
        }.get(i.status, "#888")
        rows.append(
            f"<tr>"
            f"<td>{i.id}</td>"
            f"<td><span class='dot' style='background:{badge}'></span>{html.escape(i.status)}</td>"
            f"<td class='count'>{i.count}</td>"
            f"<td>{html.escape(i.level)}</td>"
            f"<td>{html.escape(i.project)}</td>"
            f"<td class='title'>{html.escape(i.title)}</td>"
            f"<td class='culprit'>{html.escape(i.culprit)}</td>"
            f"</tr>"
        )
    body = "".join(rows) or "<tr><td colspan='7'>no issues yet</td></tr>"
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>Sentinel — Issues</title>
<style>
 body {{ font-family: -apple-system, system-ui, sans-serif; margin: 2rem; color:#1a1523; }}
 h1 {{ font-size: 1.3rem; }}
 table {{ border-collapse: collapse; width: 100%; font-size: .9rem; }}
 th, td {{ text-align: left; padding: .5rem .6rem; border-bottom: 1px solid #eee; }}
 th {{ color:#6b6b6b; font-weight:600; }}
 .count {{ font-variant-numeric: tabular-nums; font-weight:700; }}
 .dot {{ display:inline-block; width:9px; height:9px; border-radius:50%; margin-right:6px; }}
 .title {{ max-width: 380px; }}
 .culprit {{ color:#6b6b6b; font-family: ui-monospace, monospace; font-size:.8rem; }}
</style></head><body>
<h1>Sentinel — {len(issues)} grouped issues</h1>
<table><thead><tr><th>#</th><th>status</th><th>events</th><th>level</th>
<th>project</th><th>title</th><th>culprit</th></tr></thead>
<tbody>{body}</tbody></table>
</body></html>"""


def build_default_app() -> FastAPI:
    cfg = load_config()
    store = Store(cfg.db_path)
    alerts = AlertManager(cfg.alert)
    return create_app(store, alerts)


# production instance (env-configured)
app = build_default_app()
