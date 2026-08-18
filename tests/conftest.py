"""Shared fixtures: a fresh in-memory store + a TestClient per test."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient

from sentinel.alerts import AlertConfig, AlertManager
from sentinel.api import create_app
from sentinel.store import Store


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def alert_config():
    # small threshold + tiny webhook capture for fast, deterministic tests
    return AlertConfig(new_issue=True, threshold=3, window_seconds=3600.0, webhook_url=None)


@pytest.fixture
def webhook_capture():
    calls = []

    def sender(url, payload):
        calls.append((url, payload))

    return calls, sender


@pytest.fixture
def alerts(alert_config):
    return AlertManager(alert_config)


@pytest.fixture
def client(store, alerts):
    app = create_app(store, alerts)
    return TestClient(app)


def make_envelope(
    *,
    event_id: str | None = None,
    project: str = "default",
    etype: str = "TypeError",
    value: str = "boom",
    frames: list | None = None,
    message: str | None = None,
    level: str = "error",
    timestamp: float | None = None,
):
    """Build a valid event-envelope dict for tests."""
    if event_id is None:
        event_id = "".join("0123456789abcdef"[i % 16] for i in range(32))
    env: dict = {
        "event_id": event_id,
        "timestamp": timestamp if timestamp is not None else time.time(),
        "platform": "node",
        "level": level,
        "project": project,
    }
    if message is not None:
        env["message"] = message
    else:
        env["exception"] = {
            "values": [
                {
                    "type": etype,
                    "value": value,
                    "stacktrace": {"frames": frames or []},
                }
            ]
        }
    return env


def frame(function, filename, lineno=1, colno=1, in_app=True, module=None):
    return {
        "function": function,
        "filename": filename,
        "module": module,
        "lineno": lineno,
        "colno": colno,
        "in_app": in_app,
    }
