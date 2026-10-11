"""FastAPI application factory."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.agents.orchestrator import mark_interrupted
from app.api.routes import router
from app.config import PROJECT_ROOT, SETTINGS
from app.db.mongo import get_db, init_db
from app.db.repositories import Repository

log = logging.getLogger("gridmind")
FRONTEND_DIST = PROJECT_ROOT / "frontend" / "dist"


@asynccontextmanager
async def lifespan(_: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    db = get_db()
    init_db(db)
    interrupted = mark_interrupted(Repository(db))
    if interrupted:
        log.info("Marked %d unfinished run(s) as interrupted (resumable)", interrupted)
    log.info("GridMind AI ready (db=%s, llm=%s)", db.name, "on" if SETTINGS.llm_enabled else "off")
    yield


def create_app() -> FastAPI:
    app = FastAPI(title="GridMind AI", version="1.0.0", lifespan=lifespan,
                  description="Multi-agent electrical circuit analysis, correction and data-center energy optimization")
    app.add_middleware(CORSMiddleware, allow_origins=list(SETTINGS.cors_origins), allow_methods=["*"],
                       allow_headers=["*"], allow_credentials=False)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("Unhandled error on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"internal error: {type(exc).__name__}"})

    app.include_router(router)
    if FRONTEND_DIST.exists():
        app.mount("/assets", StaticFiles(directory=FRONTEND_DIST / "assets"), name="assets")

        @app.get("/{path:path}", include_in_schema=False)
        async def spa(path: str) -> FileResponse:
            target = FRONTEND_DIST / path
            if path and target.is_file() and FRONTEND_DIST in target.resolve().parents:
                return FileResponse(target)
            return FileResponse(FRONTEND_DIST / "index.html")
    return app


app = create_app()
