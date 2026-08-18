"""Small bench helpers: percentiles + a realistic mixed event stream."""

from __future__ import annotations

import random
from typing import List

from sentinel.models import EventEnvelope

STREAM_SEED = 2024
_HEX = "0123456789abcdef"


def percentile(values: List[float], pct: float) -> float:
    """Nearest-rank percentile on a copy of `values` (pct in [0, 100])."""
    if not values:
        return 0.0
    s = sorted(values)
    k = max(0, min(len(s) - 1, int(round((pct / 100.0) * (len(s) - 1)))))
    return s[k]


def summarize(values: List[float]) -> dict:
    s = sorted(values)
    return {
        "n": len(s),
        "min_ms": round(s[0], 4) if s else 0.0,
        "p50_ms": round(percentile(s, 50), 4),
        "p95_ms": round(percentile(s, 95), 4),
        "p99_ms": round(percentile(s, 99), 4),
        "max_ms": round(s[-1], 4) if s else 0.0,
        "mean_ms": round(sum(s) / len(s), 4) if s else 0.0,
    }


# A catalogue of distinct root causes for the mixed stream (each is one true issue).
_CAUSES = [
    ("TypeError", "Cannot read properties of undefined (reading 'id')",
     [("handleRequest", "src/server/handler.js"), ("renderUserCard", "src/components/UserCard.js")]),
    ("TypeError", "Cannot read properties of undefined (reading 'toFixed')",
     [("handleRequest", "src/server/handler.js"), ("formatPrice", "src/utils/money.js")]),
    ("RangeError", "Maximum call stack size exceeded",
     [("traverse", "src/services/tree.js"), ("walk", "src/services/tree.js")]),
    ("ReferenceError", "process is not defined",
     [("bootstrap", "src/index.js"), ("loadConfig", "src/config/loader.js")]),
    ("PaymentError", "charge declined",
     [("handleCheckout", "src/routes/checkout.js"), ("processPayment", "src/payments/stripe.js")]),
    ("UnauthorizedError", "no token provided",
     [("authMiddleware", "src/middleware/auth.js"), ("verify", "src/security/jwt.js")]),
    ("ValidationError", "field 'email' is required",
     [("validateBody", "src/http/validate.js"), ("createUser", "src/services/user.js")]),
    ("TimeoutError", "upstream timed out",
     [("fetchInventory", "src/services/inventory.js"), ("httpGet", "src/lib/http.js")]),
    ("SyntaxError", "Unexpected token < in JSON",
     [("parseResponse", "src/lib/json.js"), ("callApi", "src/services/api.js")]),
    ("NotFoundError", "order not found",
     [("getOrder", "src/services/order.js"), ("loadOrder", "src/db/orders.js")]),
    ("ConflictError", "duplicate key value",
     [("insertRow", "src/db/pg.js"), ("saveCart", "src/services/cart.js")]),
    ("TypeError", "x.map is not a function",
     [("renderList", "src/components/List.js"), ("mapItems", "src/components/List.js")]),
]


def _eid(rng: random.Random) -> str:
    return "".join(rng.choice(_HEX) for _ in range(32))


def mixed_stream(n: int, n_causes: int = 12, seed: int = STREAM_SEED) -> List[EventEnvelope]:
    """A realistic mixed error stream: `n` events over `n_causes` root causes with a
    skewed (power-law-ish) volume distribution + per-event line/col + message jitter.
    """
    rng = random.Random(seed)
    causes = _CAUSES[:n_causes]
    # skewed weights: first causes are much noisier than the tail
    weights = [1.0 / (i + 1) for i in range(len(causes))]
    ts = 1_723_500_000.0
    out: List[EventEnvelope] = []
    for _ in range(n):
        etype, value, frames_spec = rng.choices(causes, weights=weights, k=1)[0]
        ts += rng.uniform(0.01, 0.5)
        frames = [
            {
                "function": fn,
                "filename": fpath,
                "lineno": rng.randint(1, 400),
                "colno": rng.randint(1, 120),
                "in_app": True,
            }
            for fn, fpath in frames_spec
        ]
        uid = rng.randint(1, 100000)
        out.append(
            EventEnvelope(
                event_id=_eid(rng),
                timestamp=ts,
                platform="node",
                level="error",
                project="checkout",
                release="app@1.4.2",
                environment="production",
                exception={"values": [{"type": etype, "value": f"{value} (uid {uid})",
                                        "stacktrace": {"frames": frames}}]},
            )
        )
    return out
