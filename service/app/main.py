"""FastAPI application factory + lifespan."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from starlette.responses import JSONResponse

from app import __version__
from app.api.v1.router import router
from app.config import settings
from app.database import init_db


class _MaxPayloadBytesMiddleware:
    """Reject oversized request bodies before they are read.

    The batch intake endpoint is unauthenticated, so every request under
    ``/api/*`` gets a hard ``content-length`` cap. Requests above the cap are
    answered with 413 and their body never reaches the application.
    """

    def __init__(self, app: FastAPI):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["path"].startswith("/api/"):
            content_length = None
            for name, value in scope.get("headers", []):
                if name.lower() == b"content-length":
                    content_length = value
                    break
            if content_length is not None:
                try:
                    size = int(content_length)
                except ValueError:
                    size = None
                if size is not None and size > settings.max_payload_bytes:
                    response = JSONResponse(
                        status_code=413, content={"detail": "Payload too large"}
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    yield


def create_app() -> FastAPI:
    application = FastAPI(
        title="TikTok Lives Receiver",
        version=__version__,
        description="Accepts batched TikTok Live events from the capturer and persists them.",
        lifespan=lifespan,
    )
    application.add_middleware(_MaxPayloadBytesMiddleware)
    application.include_router(router, prefix="/api/v1")
    return application


app = create_app()