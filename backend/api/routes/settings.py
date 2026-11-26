"""Application settings (theme, retrieval/chunking defaults, task limits)."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from backend.api.deps import RuntimeDep
from backend.api.schemas import SettingsPayload
from backend.core.config import AppConfig, ChunkingSettings, RetrievalSettings

router = APIRouter(prefix="/settings", tags=["settings"])


@router.get("")
def get_settings(runtime: RuntimeDep) -> dict:
    config = runtime.config
    return {
        "theme": config.theme,
        "language": config.language,
        "retrieval": config.retrieval.model_dump(),
        "chunking": config.chunking.model_dump(),
        "max_task_steps": config.max_task_steps,
        "task_budget_usd": config.task_budget_usd,
        "data_dir": str(runtime.paths.root),
        "secret_backend": runtime.secrets.backend,
    }


@router.put("")
def put_settings(payload: SettingsPayload, runtime: RuntimeDep) -> dict:
    current = runtime.config.model_dump()
    if payload.theme is not None:
        current["theme"] = payload.theme
    if payload.retrieval is not None:
        try:
            current["retrieval"] = RetrievalSettings.model_validate(
                {**current["retrieval"], **payload.retrieval}
            ).model_dump()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.chunking is not None:
        try:
            current["chunking"] = ChunkingSettings.model_validate(
                {**current["chunking"], **payload.chunking}
            ).model_dump()
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    if payload.max_task_steps is not None:
        if payload.max_task_steps < 1:
            raise HTTPException(status_code=400, detail="max_task_steps 必须大于 0")
        current["max_task_steps"] = payload.max_task_steps
    if payload.task_budget_usd is not None:
        if payload.task_budget_usd < 0:
            raise HTTPException(status_code=400, detail="task_budget_usd 不能为负")
        current["task_budget_usd"] = payload.task_budget_usd

    try:
        config = AppConfig.model_validate(current)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    runtime.save_config(config)
    return get_settings(runtime)