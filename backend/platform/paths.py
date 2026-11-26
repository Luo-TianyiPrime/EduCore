"""Filesystem layout for EduCore.

The application keeps all user data in a single data directory.  The location
can be overridden with ``EDUCORE_DATA_DIR`` (used by tests and portable
installations); otherwise it lives under the per-user application data folder.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path

APP_NAME = "EduCore"
DATA_DIR_ENV = "EDUCORE_DATA_DIR"


def default_app_dir() -> Path:
    """Per-user directory for application-level state (config pointer, logs)."""
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = str(Path.home() / "Library" / "Application Support")
    else:
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / APP_NAME


def default_data_dir() -> Path:
    return default_app_dir() / f"{APP_NAME}-Data"


def _pointer_file() -> Path:
    return default_app_dir() / "config.json"


def resolve_data_dir(explicit: Path | str | None = None) -> Path:
    """Resolve the data directory without creating it.

    Precedence: explicit argument > ``EDUCORE_DATA_DIR`` > saved pointer > default.
    """
    if explicit:
        return Path(explicit).expanduser().resolve()
    env_value = os.environ.get(DATA_DIR_ENV)
    if env_value:
        return Path(env_value).expanduser().resolve()
    pointer = _pointer_file()
    if pointer.is_file():
        try:
            payload = json.loads(pointer.read_text(encoding="utf-8"))
            saved = payload.get("data_dir")
            if saved:
                return Path(saved).expanduser().resolve()
        except (OSError, ValueError):
            # A broken pointer must not prevent startup; fall back to the default.
            pass
    return default_data_dir()


def save_data_dir_pointer(data_dir: Path) -> None:
    """Persist a user-chosen data directory for future launches."""
    pointer = _pointer_file()
    pointer.parent.mkdir(parents=True, exist_ok=True)
    tmp = pointer.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps({"data_dir": str(data_dir)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(tmp, pointer)


@dataclass(frozen=True, slots=True)
class DataPaths:
    """Concrete paths inside the data directory."""

    root: Path

    @property
    def config_dir(self) -> Path:
        return self.root / "config"

    @property
    def database_file(self) -> Path:
        return self.root / "database" / "educore.sqlite"

    @property
    def vectors_dir(self) -> Path:
        return self.root / "vectors"

    @property
    def cache_dir(self) -> Path:
        return self.root / "cache"

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def artifacts_dir(self) -> Path:
        return self.root / "artifacts"

    @property
    def templates_dir(self) -> Path:
        return self.root / "templates"

    @property
    def logs_dir(self) -> Path:
        return self.root / "logs"

    def ensure(self) -> DataPaths:
        for path in (
            self.root,
            self.config_dir,
            self.database_file.parent,
            self.vectors_dir,
            self.cache_dir,
            self.tasks_dir,
            self.artifacts_dir,
            self.templates_dir,
            self.logs_dir,
        ):
            path.mkdir(parents=True, exist_ok=True)
        return self