import json
import re
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI

from app.core.settings import KST, REPO_ROOT, Settings

# conftest 의 app_factory 픽스처: (now=..., **설정 덮어쓰기) → FastAPI
AppFactory = Callable[..., FastAPI]

# 9장 예시의 계산 시각(2026-10-07 수요일, 예보 시간 안)
FIXED_NOW = datetime(2026, 10, 7, 7, 31, 20, tzinfo=KST)
HEALTH_TOKEN = "test-health-token-0123456789abcdefghijk"  # 32자 이상
API = "/api/v1"
STATION = "235000392"
DESTINATION = "jamsil"
CONTRACT_PATH = REPO_ROOT / "docs" / "api-contract.md"


class FakeClock:
    """호출 제한 테스트용 단조 시계(초)."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_settings(data_dir: Path, **overrides: Any) -> Settings:
    # .env 를 읽지 않고, 테스트에 필요한 값은 모두 직접 넣는다(환경변수보다 우선).
    values: dict[str, Any] = {
        "collect_data_dir": str(data_dir),
        "health_detail_token": HEALTH_TOKEN,
        "fake_data": True,
        "cors_allow_origins": "",
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def assert_error(response: Any, status_code: int, code: str) -> dict[str, Any]:
    assert response.status_code == status_code
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]
    assert isinstance(body["error"]["details"], list)
    return body


def contract_example_snapshot() -> dict[str, Any]:
    """docs/api-contract.md 9장의 json 코드 블록."""
    text = CONTRACT_PATH.read_text(encoding="utf-8")
    section = text.split("## 9.", 1)[1].split("\n## ", 1)[0]
    match = re.search(r"```json\s*\n(.*?)```", section, re.S)
    assert match is not None, "9장에 json 코드 블록이 없다"
    return json.loads(match.group(1))
