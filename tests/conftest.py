from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture()
def data_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "EduCore-Data"
    directory.mkdir()
    return directory


@pytest.fixture()
def runtime(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from backend.core.runtime import AppRuntime

    # Keep tests away from the real OS credential store.
    monkeypatch.setattr(
        "backend.platform.secrets.SecretStore._load_keyring", lambda self: None
    )
    instance = AppRuntime.create(data_dir)
    yield instance


@pytest.fixture()
def client(data_dir: Path, monkeypatch: pytest.MonkeyPatch):
    from fastapi.testclient import TestClient

    from backend.api.app import create_app

    monkeypatch.setattr(
        "backend.platform.secrets.SecretStore._load_keyring", lambda self: None
    )
    app = create_app(data_dir)
    with TestClient(app) as test_client:
        yield test_client