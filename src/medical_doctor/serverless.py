"""ASGI entry point for serverless platforms (Vercel, AWS Lambda adapters, ...).

The MCP session manager must be started through the ASGI lifespan before it can serve
requests, and it can only be started once. Serverless runtimes do not always send lifespan
events, and some run each invocation on a fresh event loop. `LazyLifespanApp` handles both:
it honours lifespan events when the platform sends them, and otherwise starts the app on the
first request and keeps it running for as long as that event loop lives (rebuilding the app
if the runtime switches to a new loop).
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

log = logging.getLogger("medical_doctor")


class LazyLifespanApp:
    def __init__(self, factory: Callable[[], Any]):
        self._factory = factory
        self._app: Any = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._ready: asyncio.Event | None = None
        self._runner: asyncio.Task[None] | None = None
        self._lock: asyncio.Lock | None = None

    @staticmethod
    def _starlette(app: Any) -> Any:
        while not hasattr(app, "router"):  # unwrap middleware (CORS) to reach the Starlette app
            app = app.app
        return app

    async def _ensure_started(self) -> None:
        loop = asyncio.get_running_loop()
        if self._loop is loop and self._runner is not None and not self._runner.done():
            await self._ready.wait()  # type: ignore[union-attr]
            return
        if self._lock is None or self._loop is not loop:
            self._lock, self._loop = asyncio.Lock(), loop
        async with self._lock:
            if self._runner is not None and not self._runner.done() and self._runner.get_loop() is loop:
                return
            app = self._factory()  # session managers are single-use: new loop, new app
            ready, stop = asyncio.Event(), asyncio.Event()
            inner = self._starlette(app)

            async def run() -> None:
                async with inner.router.lifespan_context(inner):
                    ready.set()
                    await stop.wait()

            self._app, self._ready = app, ready
            self._runner = loop.create_task(run())
            done, _ = await asyncio.wait({self._runner, loop.create_task(ready.wait())}, return_when=asyncio.FIRST_COMPLETED)
            if self._runner in done:  # startup failed: surface the error
                self._runner.result()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "lifespan":
            # Platform supports lifespan: start eagerly, then acknowledge.
            while True:
                message = await receive()
                if message["type"] == "lifespan.startup":
                    try:
                        await self._ensure_started()
                    except Exception as exc:
                        log.exception("startup failed")
                        await send({"type": "lifespan.startup.failed", "message": str(exc)})
                        return
                    await send({"type": "lifespan.startup.complete"})
                elif message["type"] == "lifespan.shutdown":
                    await send({"type": "lifespan.shutdown.complete"})
                    return
        await self._ensure_started()
        await self._app(scope, receive, send)


def create_serverless_app(settings: Any = None) -> ASGIApp:
    from .config import load_settings
    from .http import create_app

    settings = settings or load_settings()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    return LazyLifespanApp(lambda: create_app(settings, host="0.0.0.0", stateless=True))
