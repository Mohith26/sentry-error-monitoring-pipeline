"""Grouping correctness over crafted traces (the core algorithm)."""

from __future__ import annotations

from sentinel.grouping import (
    GroupingConfig,
    compute_fingerprint,
    normalize_function,
    normalize_message,
    normalize_module,
)
from sentinel.models import EventEnvelope

from .conftest import frame, make_envelope


def _fp(env: dict) -> str:
    return compute_fingerprint(EventEnvelope(**env)).hash


# ---- same cause -> SAME fingerprint --------------------------------------
def test_same_stack_different_line_numbers_group():
    a = make_envelope(frames=[frame("render", "src/a.js", lineno=10, colno=3)])
    b = make_envelope(
        event_id="a" * 32, frames=[frame("render", "src/a.js", lineno=42, colno=99)]
    )
    assert _fp(a) == _fp(b)


def test_same_stack_different_memory_address_in_message_group():
    a = make_envelope(
        etype="RangeError", value="stack exceeded near 0xdeadbeef",
        frames=[frame("walk", "src/tree.js", lineno=5)],
    )
    b = make_envelope(
        event_id="b" * 32, etype="RangeError", value="stack exceeded near 0xcafef00d",
        frames=[frame("walk", "src/tree.js", lineno=7)],
    )
    assert _fp(a) == _fp(b)


def test_message_only_user_data_varies_group():
    a = make_envelope(message="User 12345 not found in region 'us-east'")
    b = make_envelope(event_id="c" * 32, message="User 67890 not found in region 'eu-west'")
    assert _fp(a) == _fp(b)


def test_async_marker_does_not_split():
    a = make_envelope(frames=[frame("processPayment", "src/pay.js", lineno=8)])
    b = make_envelope(
        event_id="d" * 32, frames=[frame("async processPayment", "src/pay.js", lineno=8)]
    )
    assert _fp(a) == _fp(b)


def test_node_modules_version_ignored_when_inapp_matches():
    a = make_envelope(frames=[
        frame("handle", "node_modules/express@4.18.2/lib/router.js", in_app=False),
        frame("authMw", "src/mw/auth.js", lineno=10),
    ])
    b = make_envelope(event_id="e" * 32, frames=[
        frame("handle", "node_modules/express@4.19.0/lib/router.js", in_app=False),
        frame("authMw", "src/mw/auth.js", lineno=20),
    ])
    assert _fp(a) == _fp(b)


# ---- different cause -> DIFFERENT fingerprint ----------------------------
def test_same_type_different_function_split():
    a = make_envelope(etype="TypeError", frames=[frame("renderUserCard", "src/UserCard.js")])
    b = make_envelope(
        event_id="1" * 32, etype="TypeError", frames=[frame("formatPrice", "src/money.js")]
    )
    assert _fp(a) != _fp(b)


def test_same_top_frame_different_deeper_frame_split():
    a = make_envelope(frames=[
        frame("handleRequest", "src/handler.js"),
        frame("applyDiscount", "src/coupon.js"),
    ])
    b = make_envelope(event_id="2" * 32, frames=[
        frame("handleRequest", "src/handler.js"),
        frame("formatPrice", "src/money.js"),
    ])
    assert _fp(a) != _fp(b)


def test_different_exception_type_split():
    a = make_envelope(etype="TypeError", frames=[frame("f", "src/x.js")])
    b = make_envelope(event_id="3" * 32, etype="ReferenceError", frames=[frame("f", "src/x.js")])
    assert _fp(a) != _fp(b)


def test_distinct_messages_split():
    a = make_envelope(message="disk is full")
    b = make_envelope(event_id="4" * 32, message="permission denied")
    assert _fp(a) != _fp(b)


# ---- determinism ----------------------------------------------------------
def test_fingerprint_is_deterministic():
    env = make_envelope(frames=[frame("a", "src/a.js"), frame("b", "src/b.js")])
    assert _fp(env) == _fp(env) == _fp(env)


# ---- in-app frame selection ----------------------------------------------
def test_prefers_in_app_frames():
    # two events differing only in library frames but identical in-app frame -> group
    a = make_envelope(frames=[
        frame("libA", "node_modules/x/lib.js", in_app=False),
        frame("myHandler", "src/handler.js", lineno=1),
    ])
    b = make_envelope(event_id="5" * 32, frames=[
        frame("libB", "node_modules/y/other.js", in_app=False),
        frame("myHandler", "src/handler.js", lineno=1),
    ])
    assert _fp(a) == _fp(b)


def test_falls_back_to_all_frames_when_none_in_app():
    a = make_envelope(frames=[frame("libA", "node_modules/x/lib.js", in_app=False)])
    b = make_envelope(
        event_id="6" * 32, frames=[frame("libB", "node_modules/y/lib.js", in_app=False)]
    )
    assert _fp(a) != _fp(b)


def test_configurable_in_app_rules():
    cfg = GroupingConfig(in_app_exclude=("vendor/",))
    env = EventEnvelope(**make_envelope(frames=[
        frame("v", "vendor/lib.js", in_app=None),
        frame("app", "src/app.js", in_app=None),
    ]))
    # with vendor/ excluded, only src/app.js is in-app
    comp = compute_fingerprint(env, cfg).components
    joined = "\n".join(comp)
    assert "vendor/lib.js" not in joined
    assert "src/app.js" in joined


# ---- normalization units --------------------------------------------------
def test_normalize_message_strips_variable_parts():
    assert normalize_message("User 12345 at 0xff for 'bob@x.com'") == normalize_message(
        "User 99 at 0x01 for 'sue@y.io'"
    )


def test_normalize_function_anonymous():
    assert normalize_function(None) == "<anonymous>"
    assert normalize_function("Object.<anonymous>") == "<anonymous>"
    assert normalize_function("async foo") == "foo"


def test_normalize_module_strips_prefix_and_version():
    assert normalize_module("/Users/x/proj/src/a.js") == "src/a.js"
    assert normalize_module("node_modules/pkg@1.2.3/index.js") == "node_modules/pkg/index.js"
    assert normalize_module("webpack:///src/b.js?v=2") == "src/b.js"
