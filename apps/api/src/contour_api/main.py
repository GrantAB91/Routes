"""Contour API application."""

from __future__ import annotations

import uuid
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import dispose_engine
from .errors import EXCEPTION_HANDLERS
from .routers import health, intent

DESCRIPTION = """
Contour plans cycling routes from sources it can name, and says plainly what it
does not know.

Three conventions run through every endpoint:

* **Unknown is a value.** Route metrics report the share of distance with no
  surface, access or elevation data. A constraint that could not be checked is
  reported as unchecked, never as met.
* **Feasibility has four states.** A route may be fully satisfied, satisfied
  with unknown data, partially satisfied, or not feasible. A route that appears
  compliant only because the data needed to check it is missing is not the same
  as one proven compliant.
* **Disabled is not broken.** A capability with no provider configured returns
  503 with the exact setting that would enable it, rather than a degraded or
  invented result.
"""


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    yield
    await dispose_engine()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="Contour API",
        version="0.1.0",
        description=DESCRIPTION,
        lifespan=lifespan,
        openapi_url="/openapi.json",
        docs_url="/docs",
    )

    # Same-origin by default. The web app's origin is configured explicitly
    # rather than reflected from the request, which would defeat the purpose.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.api_base_url, "http://127.0.0.1:3000", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["authorization", "content-type", "idempotency-key"],
    )

    @app.middleware("http")
    async def request_id(request: Request, call_next):
        """Attach a request id to every response for tracing (§19.4, §23.1)."""
        incoming = request.headers.get("x-request-id")
        request.state.request_id = incoming or uuid.uuid4().hex
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    for exception_type, handler in EXCEPTION_HANDLERS.items():
        app.add_exception_handler(exception_type, handler)

    app.include_router(health.router)
    app.include_router(intent.router)

    return app


app = create_app()
