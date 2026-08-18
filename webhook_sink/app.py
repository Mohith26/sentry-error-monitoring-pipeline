"""A tiny webhook receiver so alert deliveries are visible in the compose demo.

POST /hook  -> record + log an incoming alert payload
GET  /hooks -> list everything received
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import FastAPI, Request

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("webhook_sink")

app = FastAPI(title="Sentinel webhook sink")
_received: List[Dict[str, Any]] = []


@app.post("/hook")
async def hook(request: Request):
    payload = await request.json()
    _received.append(payload)
    logger.info("ALERT WEBHOOK %s", payload)
    return {"ok": True, "received": len(_received)}


@app.get("/hooks")
def hooks():
    return {"count": len(_received), "hooks": _received}


@app.get("/health")
def health():
    return {"status": "ok", "received": len(_received)}
