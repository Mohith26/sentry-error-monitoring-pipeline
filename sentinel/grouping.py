"""Stack-trace fingerprinting — the core grouping algorithm.

Turns a flood of raw error events into a deduplicated set of issues by computing a
*deterministic* fingerprint from the normalized stack trace, so the same root cause
across thousands of events collapses to one issue while distinct causes stay apart.

Strategy (mirrors Sentry's default grouping, simplified):
  1. If the exception has a stack trace, fingerprint the FRAME SIGNATURE
     (per-frame: normalized module + normalized function), preferring in-app frames.
     Line/column numbers are excluded -> "same stack, different line numbers" groups.
  2. Otherwise fall back to exception type + normalized message.
  3. Otherwise fall back to the normalized log message.

Normalization strips the volatile parts that must NOT split a group: line/col numbers,
absolute path prefixes, node_modules versions, and — for message-based grouping —
memory addresses, hex, UUIDs, integers, quoted user data, and emails.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from typing import List, Optional

from .models import EventEnvelope, ExceptionValue, Frame

# ---- message normalization (order matters: specific patterns before generic) ----
_RE_HEX_ADDR = re.compile(r"0x[0-9a-fA-F]+")
_RE_UUID = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_RE_EMAIL = re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")
_RE_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")
_RE_LONG_HEX = re.compile(r"\b[0-9a-fA-F]{16,}\b")
# A digit run that starts at a boundary (space, punctuation, start-of-string) but may
# be followed by a unit suffix ("2000ms", "512MB"). Won't touch digits embedded in a
# stable token ("utf8", "sha256", "s3") whose digits follow a word char.
_RE_NUMBER = re.compile(r"(?<![\w.])\d+")
_RE_WS = re.compile(r"\s+")

# ---- path normalization ----
_RE_QUERY_HASH = re.compile(r"[?#].*$")
_RE_NODE_MODULES_VERSION = re.compile(r"@\d+\.\d+\.\d+")
_RE_SCHEME = re.compile(r"^[a-z]+://")
_RE_ANON_SUFFIX = re.compile(r"\d+$")

_DEFAULT_IN_APP_EXCLUDE = ("node_modules/", "node:internal", "internal/")
_DEFAULT_PATH_PREFIXES = ("/src/", "/dist/", "/build/", "/lib/", "/app/")


@dataclass(frozen=True)
class GroupingConfig:
    """Configurable in-app frame rules. Immutable."""

    in_app_exclude: tuple = _DEFAULT_IN_APP_EXCLUDE
    in_app_include: tuple = ()
    # path prefixes that mark the start of the app-relative portion of a filename
    app_path_markers: tuple = _DEFAULT_PATH_PREFIXES


DEFAULT_CONFIG = GroupingConfig()


@dataclass(frozen=True)
class Fingerprint:
    hash: str
    culprit: str
    kind: str  # "stacktrace" | "type+message" | "message"
    components: tuple = field(default_factory=tuple)


def normalize_message(msg: Optional[str]) -> str:
    """Collapse the volatile, per-event parts of a message so variants group.

    "User 12345 not found" and "User 67890 not found" -> same normalized token.
    """
    if not msg:
        return ""
    s = msg.strip()
    s = _RE_HEX_ADDR.sub("<addr>", s)
    s = _RE_UUID.sub("<uuid>", s)
    s = _RE_EMAIL.sub("<email>", s)
    s = _RE_LONG_HEX.sub("<hex>", s)
    s = _RE_QUOTED.sub("<str>", s)
    s = _RE_NUMBER.sub("<num>", s)
    s = _RE_WS.sub(" ", s)
    return s.strip()


def normalize_function(fn: Optional[str]) -> str:
    """Normalize a frame's function name."""
    if not fn:
        return "<anonymous>"
    f = fn.strip()
    if f in ("?", "<anonymous>", "<unknown>", "eval"):
        return "<anonymous>"
    # strip common JS wrappers: "Object.<anonymous>", "Module._compile"
    if f.endswith(".<anonymous>"):
        return "<anonymous>"
    # strip a leading "async " marker (async boundary should not split a group)
    if f.startswith("async "):
        f = f[len("async ") :]
    return f


def normalize_module(path: Optional[str]) -> str:
    """Normalize a frame's module/filename to a stable, machine-independent key.

    Strips URL schemes, query/hash, node_modules versions, and absolute path
    prefixes so the same source location matches across builds and machines.
    """
    if not path:
        return "<unknown>"
    p = path.strip().replace("\\", "/")
    p = _RE_SCHEME.sub("", p)
    p = _RE_QUERY_HASH.sub("", p)
    p = _RE_NODE_MODULES_VERSION.sub("", p)

    # node:internal/process/task_queues -> node:internal/process/task_queues (kept)
    if p.startswith("node:"):
        return p

    # If a node_modules segment exists, keep from the last node_modules onward
    # (collapses machine-specific install prefix but keeps the package path).
    idx = p.rfind("node_modules/")
    if idx != -1:
        return p[idx:]

    # Strip an absolute machine prefix by anchoring on a known app marker.
    for marker in DEFAULT_CONFIG.app_path_markers:
        m = p.find(marker)
        if m != -1:
            return p[m + 1 :]  # drop the leading slash, keep "src/..."

    # Fall back to a leading-slash-stripped path (already app-relative).
    return p.lstrip("/")


def apply_in_app(frame: Frame, config: GroupingConfig) -> bool:
    """Decide whether a frame is in-app. Explicit SDK in_app wins; otherwise rules."""
    if frame.in_app is not None:
        return frame.in_app
    mod = normalize_module(frame.module or frame.filename)
    for pat in config.in_app_include:
        if pat in mod:
            return True
    for pat in config.in_app_exclude:
        if pat in mod:
            return False
    return True


def _select_frames(frames: List[Frame], config: GroupingConfig) -> List[Frame]:
    """Prefer in-app frames; fall back to all frames if none are in-app."""
    in_app = [f for f in frames if apply_in_app(f, config)]
    return in_app if in_app else frames


def _frame_component(frame: Frame) -> str:
    return f"{normalize_module(frame.module or frame.filename)}:{normalize_function(frame.function)}"


def _sha1(parts: List[str]) -> str:
    h = hashlib.sha1()
    h.update("\n".join(parts).encode("utf-8"))
    return h.hexdigest()


def _pick_exception(event: EventEnvelope) -> Optional[ExceptionValue]:
    if event.exception and event.exception.values:
        # the most recently raised exception is the last in the chain
        return event.exception.values[-1]
    return None


def _derive_culprit(frames: List[Frame], exc: Optional[ExceptionValue]) -> str:
    """Human-readable 'where it broke' — the top in-app frame."""
    if frames:
        top = frames[-1]  # frames are ordered oldest-first (call -> crash)
        mod = normalize_module(top.module or top.filename)
        fn = normalize_function(top.function)
        return f"{fn} ({mod})"
    if exc and exc.type:
        return exc.type
    return "<unknown>"


def compute_fingerprint(
    event: EventEnvelope, config: GroupingConfig = DEFAULT_CONFIG
) -> Fingerprint:
    """Deterministically fingerprint an event into an issue key."""
    exc = _pick_exception(event)

    if exc and exc.stacktrace and exc.stacktrace.frames:
        selected = _select_frames(exc.stacktrace.frames, config)
        components = [_frame_component(f) for f in selected]
        parts = [f"type:{exc.type or '<none>'}"] + components
        return Fingerprint(
            hash=_sha1(parts),
            culprit=_derive_culprit(selected, exc),
            kind="stacktrace",
            components=tuple(parts),
        )

    if exc and (exc.type or exc.value):
        parts = [f"type:{exc.type or '<none>'}", f"value:{normalize_message(exc.value)}"]
        return Fingerprint(
            hash=_sha1(parts),
            culprit=exc.type or "<unknown>",
            kind="type+message",
            components=tuple(parts),
        )

    parts = [f"message:{normalize_message(event.message)}"]
    return Fingerprint(
        hash=_sha1(parts),
        culprit=(event.transaction or "<message>"),
        kind="message",
        components=tuple(parts),
    )


def issue_title(event: EventEnvelope) -> str:
    """A short display title for the grouped issue."""
    exc = _pick_exception(event)
    if exc and exc.type:
        val = (exc.value or "").strip()
        return f"{exc.type}: {val}" if val else exc.type
    if event.message:
        return event.message.strip()[:200]
    return "<unknown error>"
