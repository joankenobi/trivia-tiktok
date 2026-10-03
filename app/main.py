"""Composition root.

Serves the JSON API and the static host console / OBS overlay from the same
process, on loopback only.

`app.frontend()` requires FastAPI >= 0.141.0. It registers the static directory
as LOW-priority routes, so real path operations are matched first and the
fallback only kicks in for unmatched HTML GETs. The older
`StaticFiles(html=True)` mounted at "/" swallows 404s and competes with the API.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.responses import FileResponse

from .api.routes import lives as lives_routes
from .api.routes import rounds as rounds_routes
from .api.routes import tally as tally_routes
from .config import WEB_DIR, settings
from .db import SessionLocal, dispose_engine, init_models
from .infra.poller import PollerRegistry
from .infra.seed import load_questions_from_file
from .services import session_service as svc
from .services.show_runner import ShowRunner
from . import main_state

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("trivia")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_models()
    questions = load_questions_from_file()  # raises QuestionBankError on a bad bank
    async with SessionLocal() as session:
        await svc.seed_questions(session, questions)
    settings.avatars_dir.mkdir(parents=True, exist_ok=True)

    main_state.registry = PollerRegistry(SessionLocal)
    app.state.registry = main_state.registry

    # The runner is created before the registry is needed by any request, and is
    # resumed after seeding so a show left mid-flight by a restart keeps ticking
    # instead of stalling on an open round.
    main_state.runner = ShowRunner(SessionLocal, main_state.registry)
    app.state.runner = main_state.runner
    resumed = await main_state.runner.resume_active_shows()
    if resumed:
        log.info("resumed autoplay for shows: %s", resumed)

    log.info("ready: %d questions, upstream=%s", len(questions), settings.lives_api_base_url)
    try:
        yield
    finally:
        # Runner first: it may be mid-close and touching the DB, and the engine
        # must outlive every in-flight task.
        if main_state.runner is not None:
            await main_state.runner.stop_all()
        if main_state.registry is not None:
            await main_state.registry.stop_all()
        await dispose_engine()


def _ships_with_the_app(path: str) -> bool:
    """True for the JS/CSS that changes on every deploy.

    Deliberately narrow. `web/avatars/` holds hundreds of content-stable images
    and is exactly where a cache earns its keep, so a blanket `no-store` would
    turn every render into 300 image requests.
    """
    return path.startswith("/js/") or path.endswith((".js", ".css"))


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    # The app's own JS and CSS are never cached, so a browser source that loads
    # the page always runs the bytes that are on disk right now.
    #
    # This matters more than it looks. An OBS browser source keeps its document
    # -- and its ES module map -- alive across a server restart, so a stale copy
    # silently outlives the fix that changed it: the API is right, the tests pass,
    # and the screen still runs code that no longer exists. Note the boundary:
    # this governs what a *fresh* page load fetches, not a document that is
    # already open. That one still needs an explicit browser-source refresh.
    #
    # `BaseHTTPMiddleware` is safe here because nothing in this app streams.
    @app.middleware("http")
    async def no_store_app_assets(request, call_next):
        response = await call_next(request)
        if _ships_with_the_app(request.url.path):
            response.headers["Cache-Control"] = "no-store"
        return response

    api = APIRouter(prefix="/api/v1")
    api.include_router(lives_routes.router)
    api.include_router(rounds_routes.router)
    api.include_router(tally_routes.router)
    app.include_router(api)

    # Explicit routes for the two surfaces. The SPA fallback only triggers for
    # requests that accept HTML, but OBS sends `*/*` for its browser source, so
    # /overlay would 404 without a real route.
    @app.get("/overlay", include_in_schema=False)
    async def overlay() -> FileResponse:
        return FileResponse(WEB_DIR / "overlay.html")

    @app.get("/control", include_in_schema=False)
    async def control() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    # Static assets (css/js/avatars) stay low-priority so API routes win.
    app.frontend("/", directory=WEB_DIR, fallback="index.html")
    return app


app = create_app()
