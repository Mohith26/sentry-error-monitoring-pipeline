"""A labeled grouping dataset with ground-truth groups (synthetic / crafted).

Each generated event carries a `true_group` label. The dataset deliberately exercises
the behaviours a grouping engine must get right — and a few genuinely hard cases so the
measured precision/recall are honest, not a rigged 100%:

  SAME cause -> SHOULD group (recall):
    - identical stack, different line/column numbers (different builds)
    - identical stack, memory addresses / user ids / emails vary in the message
    - message-only errors whose variable parts (shard #, ms, ids) differ
    - an `async ` marker on a frame function (async boundary must not split)
    - a library (node_modules) version differs but in-app frames match

  DIFFERENT cause -> SHOULD stay apart (precision):
    - same exception *type* but a different function/file
    - two distinct bugs that share the same *top* frame but differ deeper
    - different exception types entirely

  HARD (honest limitation):
    - one true group where half the events carry an extra in-app "retry wrapper"
      frame -> a frame-signature grouper will *false-split* these. Included on purpose
      so recall reflects a real weakness rather than a curated best case.

Everything is seeded and deterministic.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import List, Optional

from sentinel.models import EventEnvelope

SEED = 1337


@dataclass(frozen=True)
class LabeledEvent:
    event: EventEnvelope
    true_group: str


_HEX = "0123456789abcdef"


def _eid(rng: random.Random) -> str:
    return "".join(rng.choice(_HEX) for _ in range(32))


def _frame(function, filename, lineno, colno, in_app=True, module=None):
    return {
        "function": function,
        "filename": filename,
        "module": module,
        "lineno": lineno,
        "colno": colno,
        "in_app": in_app,
    }


def _exc_event(rng, ts, project, etype, value, frames):
    return EventEnvelope(
        event_id=_eid(rng),
        timestamp=ts,
        platform="node",
        level="error",
        project=project,
        release="app@1.4.2",
        environment="production",
        exception={"values": [{"type": etype, "value": value, "stacktrace": {"frames": frames}}]},
        tags={"runtime": "node20"},
    )


def _msg_event(rng, ts, project, message):
    return EventEnvelope(
        event_id=_eid(rng),
        timestamp=ts,
        platform="node",
        level="error",
        project=project,
        release="app@1.4.2",
        environment="production",
        message=message,
    )


def build_dataset(project: str = "checkout") -> List[LabeledEvent]:
    rng = random.Random(SEED)
    out: List[LabeledEvent] = []
    ts = 1_723_000_000.0

    def add(ev, group):
        nonlocal ts
        ts += rng.uniform(0.1, 5.0)
        out.append(LabeledEvent(ev, group))

    def jitter_line(base):  # different builds shift line/col numbers
        return base + rng.randint(-4, 4), rng.randint(1, 60)

    # ---- G1: TypeError reading undefined property in a component (12 variants) ----
    for _ in range(12):
        l1, c1 = jitter_line(48)
        l2, c2 = jitter_line(112)
        uid = rng.randint(1000, 9999)
        prop = rng.choice(["name", "email", "avatar", "id"])
        frames = [
            _frame("handleRequest", "src/server/handler.js", *jitter_line(30)),
            _frame("renderProfile", "src/components/Profile.js", l2, c2),
            _frame("renderUserCard", "src/components/UserCard.js", l1, c1),
        ]
        ev = _exc_event(
            rng, ts, project, "TypeError",
            f"Cannot read properties of undefined (reading '{prop}') for user {uid}",
            frames,
        )
        add(ev, "G1_usercard_undefined")

    # ---- G2: same TypeError *type* but different function/file -> distinct ----
    for _ in range(9):
        frames = [
            _frame("handleRequest", "src/server/handler.js", *jitter_line(30)),
            _frame("formatPrice", "src/utils/money.js", *jitter_line(77)),
        ]
        ev = _exc_event(
            rng, ts, project, "TypeError",
            "Cannot read properties of undefined (reading 'toFixed')",
            frames,
        )
        add(ev, "G2_money_undefined")

    # ---- G3: RangeError recursion; memory addresses vary in the message ----
    for _ in range(8):
        addr = "0x" + "".join(rng.choice(_HEX) for _ in range(8))
        frames = [
            _frame("traverse", "src/services/tree.js", *jitter_line(52)),
            _frame("walk", "src/services/tree.js", *jitter_line(61)),
            _frame("walk", "src/services/tree.js", *jitter_line(61)),
        ]
        ev = _exc_event(
            rng, ts, project, "RangeError",
            f"Maximum call stack size exceeded near {addr}",
            frames,
        )
        add(ev, "G3_tree_recursion")

    # ---- G4: message-only DB timeout; shard # and ms vary -> normalize+group ----
    for _ in range(10):
        shard = rng.randint(1, 32)
        ms = rng.choice([1000, 1500, 2000, 2500, 3000])
        add(_msg_event(rng, ts, project, f"Connection to db-shard-{shard} timed out after {ms}ms"),
            "G4_db_timeout")

    # ---- G5: message-only validation; quoted email varies -> group ----
    for _ in range(7):
        email = f"user{rng.randint(1,999)}@{rng.choice(['gmail.com','corp.io','x.co'])}"
        add(_msg_event(rng, ts, project, f"Invalid email address '{email}' rejected by validator"),
            "G5_invalid_email")

    # ---- G6: same cause, but half carry an `async ` marker on a frame -> must group ----
    for i in range(8):
        fn = "async processPayment" if i % 2 == 0 else "processPayment"
        frames = [
            _frame("handleCheckout", "src/routes/checkout.js", *jitter_line(24)),
            _frame(fn, "src/payments/stripe.js", *jitter_line(88)),
        ]
        ev = _exc_event(
            rng, ts, project, "PaymentError",
            f"charge declined (code {rng.choice(['card_declined','insufficient_funds'])})",
            frames,
        )
        add(ev, "G6_payment_async")

    # ---- G7: node_modules library version differs but in-app frames match -> group ----
    for i in range(7):
        ver = rng.choice(["4.18.2", "4.19.0", "4.17.3"])
        frames = [
            _frame("handle", f"node_modules/express@{ver}/lib/router/index.js",
                    *jitter_line(280), in_app=False),
            _frame("authMiddleware", "src/middleware/auth.js", *jitter_line(15)),
        ]
        ev = _exc_event(
            rng, ts, project, "UnauthorizedError",
            "no token provided", frames,
        )
        add(ev, "G7_auth_middleware")

    # ---- G8: distinct bug sharing G2's TOP frame but different deeper frame ----
    #        (top frame identical to G2's handleRequest; must NOT merge with G2)
    for _ in range(6):
        frames = [
            _frame("handleRequest", "src/server/handler.js", *jitter_line(30)),
            _frame("applyDiscount", "src/utils/coupon.js", *jitter_line(41)),
        ]
        ev = _exc_event(
            rng, ts, project, "TypeError",
            "Cannot read properties of null (reading 'percent')",
            frames,
        )
        add(ev, "G8_coupon_null")

    # ---- G9: ReferenceError, distinct ----
    for _ in range(6):
        frames = [
            _frame("bootstrap", "src/index.js", *jitter_line(12)),
            _frame("loadConfig", "src/config/loader.js", *jitter_line(33)),
        ]
        ev = _exc_event(
            rng, ts, project, "ReferenceError",
            "process is not defined", frames,
        )
        add(ev, "G9_config_reference")

    # ---- G10: HARD false-split trap: same cause, half have an extra retry wrapper ----
    for i in range(8):
        base = [
            _frame("handleWebhook", "src/routes/webhook.js", *jitter_line(19)),
            _frame("verifySignature", "src/security/hmac.js", *jitter_line(44)),
        ]
        if i % 2 == 0:
            base.insert(1, _frame("withRetry", "src/lib/retry.js", *jitter_line(8)))
        ev = _exc_event(
            rng, ts, project, "SignatureError",
            "hmac signature mismatch", base,
        )
        add(ev, "G10_webhook_signature")

    return out
