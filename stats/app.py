"""HTTP side: the app posts batches, the console shows the numbers.

POST   /v1/events            a batch from one device (closed vocabulary, see vocabulary.py)
DELETE /v1/devices/{id}      "Effacer mes statistiques" in the app's settings
GET    /console              the dashboard, behind ADMIN_TOKEN (HTTP Basic, any user name)
GET    /health               for the container
"""

import os
import re
import secrets
import time
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from . import console, metrics
from .store import Store
from .vocabulary import MAX_EVENTS_PER_BATCH, clean

UUID = re.compile(r"^[0-9A-Fa-f]{8}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{4}-[0-9A-Fa-f]{12}$")
# Envelope fields: short, shaped values only. Anything else is stored as null.
SHAPES = {
    "app": re.compile(r"^\d{1,3}(\.\d{1,3}){0,2} \(\d{1,6}\)$"),   # "1.0 (3)"
    "os": re.compile(r"^\d{1,2}(\.\d{1,2}){0,2}$"),               # "17.5"
    "model": re.compile(r"^(iPhone|iPad|iPod)\d{1,2},\d{1,2}$|^arm64$|^x86_64$"),
    "lang": re.compile(r"^[a-z]{2}$"),
}
MAX_CLOCK_SKEW = 7 * 86400


def create_app(store: Store | None = None, admin_token: str | None = None) -> FastAPI:
    store = store or Store(Path(os.environ.get("DATA_DIR", "/data")) / "stats.sqlite3")
    token = admin_token if admin_token is not None else os.environ.get("ADMIN_TOKEN", "")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    basic = HTTPBasic()

    def admin(credentials: HTTPBasicCredentials = Depends(basic)) -> None:
        if not token or not secrets.compare_digest(credentials.password.encode(), token.encode()):
            raise HTTPException(401, headers={"WWW-Authenticate": "Basic"})

    @app.get("/health")
    def health() -> dict:
        return {"ok": True}

    @app.post("/v1/events", status_code=204)
    async def events(request: Request) -> Response:
        try:
            body = await request.json()
        except ValueError:
            raise HTTPException(400) from None
        if not isinstance(body, dict) or not isinstance(body.get("device"), str) or not UUID.match(body["device"]):
            raise HTTPException(400)
        raw = body.get("events")
        if not isinstance(raw, list) or len(raw) > MAX_EVENTS_PER_BATCH:
            raise HTTPException(400)
        envelope = {k: (v if isinstance(v := body.get(k), str) and rule.match(v) else None)
                    for k, rule in SHAPES.items()}
        envelope["test"] = 1 if body.get("test") is True else 0
        country = request.headers.get("cf-ipcountry", "").upper()
        # XX and T1 are Cloudflare's placeholders (unknown, Tor), not countries.
        envelope["country"] = country if re.fullmatch(r"[A-Z]{2}", country) and country not in ("XX", "T1") else None
        now = int(time.time())
        kept, dropped = [], []
        for item in raw:
            if not isinstance(item, dict) or not isinstance(item.get("name"), str):
                continue
            props = clean(item["name"], item.get("props"))
            if props is None:
                dropped.append(item["name"])
                continue
            at = item.get("at")
            # The phone's clock, unless it is absurd: then the arrival time.
            at = int(at) if isinstance(at, (int, float)) and abs(at - now) <= MAX_CLOCK_SKEW else now
            kept.append((item["name"], at, props))
        store.record(body["device"].lower(), envelope, kept, dropped, now=now)
        return Response(status_code=204)

    @app.delete("/v1/devices/{device}", status_code=204)
    def forget(device: str) -> Response:
        if not UUID.match(device):
            raise HTTPException(400)
        store.forget(device.lower())
        return Response(status_code=204)

    @app.get("/console", response_class=HTMLResponse, dependencies=[Depends(admin)])
    def dashboard(tests: int = 0) -> str:
        return console.render(metrics.compute(store, include_tests=bool(tests)), include_tests=bool(tests))

    return app
