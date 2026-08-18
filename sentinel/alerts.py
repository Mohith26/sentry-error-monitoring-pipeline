"""Alert rules + delivery.

Two rule kinds (mirroring Sentry's most common alerts):
  - "new_issue"  : fire the first time a fingerprint is ever seen.
  - "threshold"  : fire when an issue's event count within a rolling window crosses N
                   (fires once on the crossing, not on every subsequent event).

Delivery sinks: an always-on in-memory/log sink (inspectable in tests) plus an
optional webhook sink (HTTP POST) when a webhook URL is configured.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .models import Issue

logger = logging.getLogger("sentinel.alerts")


@dataclass(frozen=True)
class AlertConfig:
    new_issue: bool = True
    threshold: Optional[int] = 10        # events per window to fire; None disables
    window_seconds: float = 60.0
    webhook_url: Optional[str] = None


@dataclass
class Alert:
    rule: str            # "new_issue" | "threshold"
    issue_id: int
    project: str
    title: str
    reason: str
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "rule": self.rule,
            "issue_id": self.issue_id,
            "project": self.project,
            "title": self.title,
            "reason": self.reason,
            "at": self.at,
        }


# A webhook sender is injected so tests can capture deliveries without real HTTP.
WebhookSender = Callable[[str, dict], None]


def _default_webhook_sender(url: str, payload: dict) -> None:
    import httpx  # local import so the core has no hard httpx dependency

    try:
        httpx.post(url, json=payload, timeout=2.0)
    except Exception as exc:  # never let a webhook failure break ingest
        logger.warning("webhook delivery failed: %s", exc)


class AlertManager:
    """Evaluates rules per stored event and delivers fired alerts.

    Threshold de-dup: once an issue has fired its threshold alert, it will not fire
    again until its windowed count drops back below N (crossing semantics).
    """

    def __init__(
        self,
        config: AlertConfig,
        webhook_sender: Optional[WebhookSender] = None,
    ):
        self.config = config
        self._webhook_sender = webhook_sender or _default_webhook_sender
        self._fired: List[Alert] = []
        self._threshold_armed: dict[int, bool] = {}  # issue_id -> below-threshold?

    @property
    def fired(self) -> List[Alert]:
        return list(self._fired)

    def evaluate(
        self, issue: Issue, is_new: bool, window_count: int
    ) -> List[Alert]:
        """Return the alerts that fire for this stored event (and deliver them)."""
        alerts: List[Alert] = []

        if self.config.new_issue and is_new:
            alerts.append(
                Alert(
                    rule="new_issue",
                    issue_id=issue.id,
                    project=issue.project,
                    title=issue.title,
                    reason="first occurrence of this fingerprint",
                )
            )

        if self.config.threshold is not None:
            n = self.config.threshold
            armed = self._threshold_armed.get(issue.id, True)  # start armed
            if window_count >= n and armed:
                alerts.append(
                    Alert(
                        rule="threshold",
                        issue_id=issue.id,
                        project=issue.project,
                        title=issue.title,
                        reason=f"{window_count} events in {int(self.config.window_seconds)}s "
                        f"(>= {n})",
                    )
                )
                self._threshold_armed[issue.id] = False
            elif window_count < n:
                self._threshold_armed[issue.id] = True  # re-arm once it cools off

        for a in alerts:
            self._deliver(a)
        return alerts

    def _deliver(self, alert: Alert) -> None:
        self._fired.append(alert)
        logger.info("ALERT %s", json.dumps(alert.to_dict()))
        if self.config.webhook_url:
            self._webhook_sender(self.config.webhook_url, alert.to_dict())
