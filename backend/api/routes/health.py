"""Health and version endpoints."""

from __future__ import annotations

from fastapi import APIRouter

from backend import __version__
from backend.api.deps import RuntimeDep
from backend.api.schemas import HealthResponse

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthResponse)
def health(runtime: RuntimeDep) -> HealthResponse:
    try:
        db_ok = runtime.db.ping()
    except Exception:
        db_ok = False
    return HealthResponse(
        status="ok" if db_ok else "degraded",
        version=__version__,
        data_dir=str(runtime.paths.root),
        database=db_ok,
    )