"""Environment-driven configuration for the running server."""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Optional

from .alerts import AlertConfig

DEFAULT_DB_PATH = "data/sentinel.sqlite"


@dataclass(frozen=True)
class ServerConfig:
    db_path: str = DEFAULT_DB_PATH
    alert: AlertConfig = AlertConfig()


def _int_or_none(v: Optional[str]) -> Optional[int]:
    if v is None or v.strip() == "" or v.strip().lower() in ("0", "off", "none"):
        return None
    return int(v)


def load_config() -> ServerConfig:
    db_path = os.environ.get("SENTINEL_DB", DEFAULT_DB_PATH)
    alert = AlertConfig(
        new_issue=os.environ.get("SENTINEL_ALERT_NEW_ISSUE", "1") not in ("0", "false"),
        threshold=_int_or_none(os.environ.get("SENTINEL_ALERT_THRESHOLD", "10")),
        window_seconds=float(os.environ.get("SENTINEL_ALERT_WINDOW", "60")),
        webhook_url=os.environ.get("SENTINEL_WEBHOOK_URL") or None,
    )
    return ServerConfig(db_path=db_path, alert=alert)
