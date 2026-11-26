"""Shared FastAPI dependencies."""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, Request

from backend.core.indexing.service import IndexService
from backend.core.runtime import AppRuntime


def get_runtime(request: Request) -> AppRuntime:
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:  # pragma: no cover - lifespan guarantees this
        raise RuntimeError("应用尚未初始化")
    return runtime


RuntimeDep = Annotated[AppRuntime, Depends(get_runtime)]

def get_index(runtime: RuntimeDep) -> IndexService:
    if runtime.index is None:  # pragma: no cover - lifespan guarantees this
        raise RuntimeError("索引服务尚未初始化")
    return runtime.index


IndexDep = Annotated[IndexService, Depends(get_index)]
