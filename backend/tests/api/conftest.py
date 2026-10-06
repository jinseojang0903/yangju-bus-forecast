from datetime import datetime
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import get_now
from app.main import create_app
from tests.api.helpers import FIXED_NOW, AppFactory, make_settings


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    path = tmp_path / "collected"
    path.mkdir()
    return path


@pytest.fixture
def app_factory(data_dir: Path) -> AppFactory:
    """create_app(설정 덮어쓰기) + 지금 시각 고정."""

    def factory(now: datetime = FIXED_NOW, **overrides: Any) -> FastAPI:
        app = create_app(make_settings(data_dir, **overrides))
        app.dependency_overrides[get_now] = lambda: now
        return app

    return factory


@pytest.fixture
def client(app_factory: AppFactory) -> TestClient:
    return TestClient(app_factory(), raise_server_exceptions=False)
