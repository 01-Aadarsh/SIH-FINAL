"""
Server launcher for IP-SAKTI.

Exists only because of a Windows-specific ordering problem: `uvicorn
api.main:app` calls `asyncio.run()` internally *before* it imports the app
module, so setting the asyncio event loop policy anywhere inside api/main.py
(or anything it imports, e.g. ingestion/indexer.py) is too late — the
(Proactor) loop already exists by the time that code runs, and psycopg's
async mode raises InterfaceError under Proactor. Confirmed by reproducing it
directly: the same app that works fine under `python -m graph "..."` (where
the policy IS set before asyncio.run(), since that script's own asyncio.run
call comes after its imports) fails every /query request under a bare
`uvicorn api.main:app` on Windows.

Setting the policy here, before uvicorn is even imported, fixes it — this
becomes the actual entrypoint on Windows. Linux/macOS have no such
constraint (this is a no-op there), so `uvicorn api.main:app --reload` still
works fine for hot-reload dev on those platforms; on Windows, use this
script instead (`--reload` isn't available this way, since uvicorn's
reloader spawns a subprocess that re-imports everything and this guard
would need to run in that subprocess too — not needed for prod/demo use).

Usage:
    python run.py
    PORT=8080 WEB_CONCURRENCY=4 python run.py
"""

from __future__ import annotations

import asyncio
import os
import sys

if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

import uvicorn

if __name__ == "__main__":
    # Defaults to 0.0.0.0, not 127.0.0.1: this binds a hosting platform's
    # actual entrypoint (Render/Railway, per the Procfile), which proxies
    # in from outside the container/VM and cannot reach a loopback-only
    # bind. That's a deliberate choice for this deployment shape, not an
    # oversight — HOST is still overridable (e.g. 127.0.0.1 for a
    # local-only run, or a specific interface) for anyone who wants to
    # restrict it.
    uvicorn.run(
        "api.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        workers=int(os.getenv("WEB_CONCURRENCY", "1")),
    )
