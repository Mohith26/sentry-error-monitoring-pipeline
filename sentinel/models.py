"""Pydantic models for the event envelope and issue store.

The envelope mirrors Sentry's event shape (exception.values[].stacktrace.frames).
Validation happens at the system boundary (`/api/store`); malformed envelopes are
rejected with a 422 before they ever reach the grouping engine.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

EVENT_ID_RE = re.compile(r"^[0-9a-f]{32}$")
VALID_LEVELS = frozenset({"fatal", "error", "warning", "info", "debug"})
VALID_STATUSES = frozenset({"unresolved", "resolved", "ignored"})


class Frame(BaseModel):
    """One stack frame. lineno/colno are captured but deliberately excluded from
    the grouping fingerprint (they are the volatile part that varies build-to-build).
    """

    function: Optional[str] = None
    filename: Optional[str] = None
    module: Optional[str] = None
    lineno: Optional[int] = None
    colno: Optional[int] = None
    in_app: Optional[bool] = None
    context_line: Optional[str] = None


class StackTrace(BaseModel):
    frames: List[Frame] = Field(default_factory=list)


class ExceptionValue(BaseModel):
    type: Optional[str] = None
    value: Optional[str] = None
    stacktrace: Optional[StackTrace] = None


class ExceptionInterface(BaseModel):
    values: List[ExceptionValue] = Field(default_factory=list)


class Breadcrumb(BaseModel):
    timestamp: Optional[float] = None
    category: Optional[str] = None
    message: Optional[str] = None
    level: Optional[str] = None
    data: Dict[str, Any] = Field(default_factory=dict)


class EventEnvelope(BaseModel):
    """The event payload the SDK POSTs to /api/store."""

    event_id: str
    timestamp: float
    platform: str = "node"
    level: str = "error"
    project: str = "default"
    logger: Optional[str] = None
    release: Optional[str] = None
    environment: Optional[str] = None
    server_name: Optional[str] = None
    transaction: Optional[str] = None
    message: Optional[str] = None
    exception: Optional[ExceptionInterface] = None
    tags: Dict[str, str] = Field(default_factory=dict)
    breadcrumbs: List[Breadcrumb] = Field(default_factory=list)
    extra: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("event_id")
    @classmethod
    def _event_id_is_hex32(cls, v: str) -> str:
        if not EVENT_ID_RE.match(v):
            raise ValueError("event_id must be 32 lowercase hex characters")
        return v

    @field_validator("level")
    @classmethod
    def _level_is_valid(cls, v: str) -> str:
        if v not in VALID_LEVELS:
            raise ValueError(f"level must be one of {sorted(VALID_LEVELS)}")
        return v

    @field_validator("timestamp")
    @classmethod
    def _timestamp_positive(cls, v: float) -> float:
        if v <= 0:
            raise ValueError("timestamp must be a positive unix timestamp")
        return v

    @model_validator(mode="after")
    def _has_payload(self) -> "EventEnvelope":
        has_exc = self.exception is not None and len(self.exception.values) > 0
        has_msg = bool(self.message and self.message.strip())
        if not has_exc and not has_msg:
            raise ValueError("event must carry an exception or a message")
        return self


class Issue(BaseModel):
    """A rolled-up group of same-fingerprint events."""

    id: int
    fingerprint: str
    project: str
    level: str
    type: Optional[str] = None
    culprit: str
    title: str
    status: str = "unresolved"
    first_seen: float
    last_seen: float
    count: int
    sample_event_id: str


class StoreResult(BaseModel):
    """Return value of /api/store."""

    id: str
    issue_id: int
    fingerprint: str
    new_issue: bool
