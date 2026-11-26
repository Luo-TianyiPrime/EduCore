"""Runtime environment information and model usage accounting."""

from __future__ import annotations

import asyncio
import importlib
import platform
import shutil
import sys

from fastapi import APIRouter, HTTPException

from backend import __version__
from backend.api.deps import RuntimeDep
from backend.api.schemas import PickPathRequest

router = APIRouter(prefix="/system", tags=["system"])


@router.get("/info")
def info(runtime: RuntimeDep) -> dict:
    usage = shutil.disk_usage(runtime.paths.root)
    return {
        "version": __version__,
        "platform": platform.platform(),
        "system": platform.system(),
        "machine": platform.machine(),
        "python": sys.version.split()[0],
        "data_dir": str(runtime.paths.root),
        "database": str(runtime.paths.database_file),
        "secret_backend": runtime.secrets.backend,
        "disk_free_bytes": usage.free,
        "disk_total_bytes": usage.total,
        "endpoint_count": len(runtime.config.endpoints),
    }


@router.get("/usage")
def usage(runtime: RuntimeDep, task_id: str | None = None) -> dict:
    return {"items": runtime.db.usage_summary(task_id)}

@router.post("/pick-path")
async def pick_path(payload: PickPathRequest) -> dict:
    """Open the OS file/folder dialog and return the chosen absolute path."""
    mode = payload.mode if payload.mode in {"directory", "file"} else "directory"
    if getattr(sys, "frozen", False):
        command = [sys.executable, "--pick-path", mode]
    else:
        command = [sys.executable, "-m", "backend.platform.dialogs", mode]

    try:
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"无法启动系统选择器: {exc}") from exc

    try:
        stdout, stderr = await asyncio.wait_for(process.communicate(), timeout=300)
    except TimeoutError as exc:
        process.kill()
        raise HTTPException(status_code=408, detail="等待目录选择超时") from exc

    if process.returncode != 0:
        detail = stderr.decode("utf-8", "replace")[:300]
        raise HTTPException(status_code=500, detail=f"选择器退出异常: {detail}")
    path = stdout.decode("utf-8", "replace").strip()
    return {"path": path or None}


@router.post("/quit")
def quit_app(runtime: RuntimeDep) -> dict:
    """Ask the launcher to stop the local service (packaged builds only)."""
    import os

    if os.environ.get("EDUCORE_LAUNCHER") != "1":
        raise HTTPException(
            status_code=400,
            detail="当前由开发服务器运行，请在终端中停止服务。",
        )
    runtime_file = runtime.paths.root / "runtime" / "shutdown.request"
    runtime_file.parent.mkdir(parents=True, exist_ok=True)
    runtime_file.write_text("quit", encoding="utf-8")
    return {"status": "shutting_down"}


@router.get("/diagnostics")
def diagnostics() -> dict:
    """Report import health for packaged builds and support diagnosis."""
    modules = [
        "lancedb",
        "pyarrow",
        "jieba",
        "pypdfium2",
        "pdfplumber",
        "docx",
        "markdown_it",
        "pydantic_ai",
        "playwright",
        "keyring",
    ]
    status: dict[str, str] = {}
    for name in modules:
        try:
            module = importlib.import_module(name)
            status[name] = str(getattr(module, "__version__", "ok"))
        except Exception as exc:  # pragma: no cover - host dependent
            status[name] = f"unavailable: {type(exc).__name__}"
    return {
        "frozen": bool(getattr(sys, "frozen", False)),
        "python": sys.version.split()[0],
        "modules": status,
    }
