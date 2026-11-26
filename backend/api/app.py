"""FastAPI application factory."""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend import __version__
from backend.api.routes import (
    artifacts,
    documents,
    health,
    jobs,
    models,
    search,
    settings,
    sources,
    system,
    tasks,
)
from backend.core.models.types import ModelError
from backend.core.runtime import AppRuntime

logger = logging.getLogger(__name__)

_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1"}
_DEV_ORIGINS = ["http://127.0.0.1:5173", "http://localhost:5173"]


def _frontend_dist() -> Path:
    import sys

    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[2]))
    return base / "frontend" / "dist"


def create_app(data_dir: Path | str | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        runtime = AppRuntime.create(data_dir)
        app.state.runtime = runtime
        logger.info("EduCore %s 已启动，数据目录 %s", __version__, runtime.paths.root)
        try:
            yield
        finally:
            await runtime.aclose()

    app = FastAPI(
        title="EduCore",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=_DEV_ORIGINS,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def local_origin_guard(request: Request, call_next):
        # Bound to 127.0.0.1, but guard state-changing requests against
        # cross-origin browser calls (DNS-rebinding style) anyway.
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            if origin:
                host = urlparse(origin).hostname
                if host not in _LOCAL_HOSTS:
                    return JSONResponse(
                        status_code=403, content={"detail": "仅允许本机来源的写操作"}
                    )
        started = time.perf_counter()
        request_id = uuid.uuid4().hex[:12]
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        response.headers["X-Elapsed-MS"] = f"{(time.perf_counter() - started) * 1000:.0f}"
        return response

    @app.exception_handler(ModelError)
    async def model_error_handler(request: Request, exc: ModelError):
        status = 502 if exc.retryable else 400
        return JSONResponse(
            status_code=status,
            content={"detail": exc.message, "code": exc.code, "extra": exc.detail},
        )

    app.include_router(health.router, prefix="/api")
    app.include_router(models.router, prefix="/api")
    app.include_router(settings.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    app.include_router(sources.router, prefix="/api")
    app.include_router(documents.router, prefix="/api")
    app.include_router(jobs.router, prefix="/api")
    app.include_router(search.router, prefix="/api")
    app.include_router(tasks.router, prefix="/api")
    app.include_router(artifacts.router, prefix="/api")

    dist = _frontend_dist()
    if (dist / "index.html").is_file():
        assets = dist / "assets"
        if assets.is_dir():
            app.mount("/assets", StaticFiles(directory=assets), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"detail": "Not Found"})
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and dist.resolve() in candidate.parents:
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app


app = create_app()