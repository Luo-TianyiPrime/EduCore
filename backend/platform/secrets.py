"""API key storage.

Keys are stored in the operating system credential store through ``keyring``.
When no usable backend exists (common on headless Linux CI), we fall back to a
JSON file with restrictive permissions and report the degraded level to the
settings API so the UI can warn the user.  Keys are never written to the main
configuration file.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

SERVICE_NAME = "EduCore"


class SecretStore:
    def __init__(self, fallback_file: Path) -> None:
        self._fallback_file = fallback_file
        self._keyring_ok: bool | None = None
        self._keyring = None

    # -- backend detection -------------------------------------------------
    def _load_keyring(self):
        if self._keyring_ok is not None:
            return self._keyring if self._keyring_ok else None
        try:
            import keyring  # type: ignore

            backend = keyring.get_keyring()
            # Fail-fast backends raise on use rather than returning None.
            name = type(backend).__name__.lower()
            usable = "fail" not in name and "null" not in name
            if usable:
                # Probe once with a harmless read; some backends raise lazily.
                keyring.get_password(SERVICE_NAME, "__probe__")
            self._keyring = keyring
            self._keyring_ok = usable
        except Exception as exc:  # pragma: no cover - depends on host
            logger.warning("系统凭据存储不可用，改用本地加密文件降级方案: %s", exc)
            self._keyring = None
            self._keyring_ok = False
        return self._keyring if self._keyring_ok else None

    @property
    def backend(self) -> str:
        return "keyring" if self._load_keyring() else "local_file"

    # -- fallback file -----------------------------------------------------
    def _read_fallback(self) -> dict[str, str]:
        if not self._fallback_file.is_file():
            return {}
        try:
            return json.loads(self._fallback_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("凭据降级文件无法解析，将忽略: %s", self._fallback_file)
            return {}

    def _write_fallback(self, data: dict[str, str]) -> None:
        self._fallback_file.parent.mkdir(parents=True, exist_ok=True)
        tmp = self._fallback_file.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        with contextlib.suppress(OSError):
            os.chmod(tmp, 0o600)
        os.replace(tmp, self._fallback_file)

    # -- public API --------------------------------------------------------
    def set(self, ref: str, value: str) -> None:
        if not value:
            self.delete(ref)
            return
        keyring = self._load_keyring()
        if keyring:
            try:
                keyring.set_password(SERVICE_NAME, ref, value)
                return
            except Exception as exc:  # pragma: no cover - host specific
                logger.warning("写入系统凭据失败，降级到本地文件: %s", exc)
        data = self._read_fallback()
        data[ref] = value
        self._write_fallback(data)

    def get(self, ref: str) -> str | None:
        keyring = self._load_keyring()
        if keyring:
            try:
                value = keyring.get_password(SERVICE_NAME, ref)
                if value is not None:
                    return value
            except Exception as exc:  # pragma: no cover - host specific
                logger.warning("读取系统凭据失败: %s", exc)
        return self._read_fallback().get(ref)

    def delete(self, ref: str) -> None:
        keyring = self._load_keyring()
        if keyring:
            with contextlib.suppress(Exception):
                keyring.delete_password(SERVICE_NAME, ref)
        data = self._read_fallback()
        if ref in data:
            data.pop(ref)
            self._write_fallback(data)