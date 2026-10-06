import json
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.settings import COLLECT_TARGET, KST, STATUS_FILENAME, target_intervals
from tests.api.helpers import API, FIXED_NOW, HEALTH_TOKEN, AppFactory, assert_error

HEALTH = f"{API}/health"
DETAIL = f"{API}/health/detail"
OUTSIDE_WINDOW = datetime(2026, 10, 7, 12, 0, tzinfo=KST)


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def write_status(
    data_dir: Path,
    now: datetime = FIXED_NOW,
    *,
    success_age_sec: int = 5,
    overrides: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """수집기 StatusStore 형식의 상태 파일(필요한 키만)."""
    last_success = _iso(now - timedelta(seconds=success_age_sec))
    targets = {
        key: {
            "interval_sec": interval,
            "calls": 100,
            "success": 98,
            "failure": 2,
            "consecutive_failures": 0,
            "empty": 3,
            "skipped_cycles": 1,
            "last_attempt_at": last_success,
            "last_success_at": last_success,
        }
        for key, interval in target_intervals(COLLECT_TARGET).items()
    }
    for key, values in (overrides or {}).items():
        targets[key].update(values)
    status = {
        "date": now.date().isoformat(),
        "last_success_at": last_success,
        "apis": {
            "buslocationservice": {"calls": 1200, "failure": 4, "quota_exceeded": 0},
            "busarrivalservice": {
                "calls": 300,
                "failure": 1,
                "quota_exceeded": 2,
                "throttled": True,
            },
        },
        "targets": targets,
    }
    (data_dir / STATUS_FILENAME).write_text(json.dumps(status), encoding="utf-8")
    return status


def health_client(
    app_factory: AppFactory, now: datetime = FIXED_NOW, **overrides: Any
) -> TestClient:
    return TestClient(app_factory(now=now, **overrides), raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------
def test_health_ok_when_recent_success(app_factory: AppFactory, data_dir: Path) -> None:
    write_status(data_dir)

    response = health_client(app_factory).get(HEALTH)

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "now": "2026-10-07T07:31:20+09:00",
        "lastSuccessAt": "2026-10-07T07:31:15+09:00",
    }


def test_health_without_status_file_is_idle(app_factory: AppFactory) -> None:
    response = health_client(app_factory).get(HEALTH)

    assert response.status_code == 200
    assert response.json()["status"] == "idle"
    assert response.json()["lastSuccessAt"] is None


def test_health_outside_window_is_idle(app_factory: AppFactory, data_dir: Path) -> None:
    write_status(data_dir, now=OUTSIDE_WINDOW, success_age_sec=3600)

    body = health_client(app_factory, now=OUTSIDE_WINDOW).get(HEALTH).json()

    assert body["status"] == "idle"
    assert body["lastSuccessAt"] == "2026-10-07T11:00:00+09:00"


def test_health_degraded_when_target_success_is_old(
    app_factory: AppFactory, data_dir: Path
) -> None:
    # G1300 주기 10초 × 3 = 30초를 넘김
    old = _iso(FIXED_NOW - timedelta(seconds=31))
    write_status(data_dir, overrides={"location:G1300": {"last_success_at": old}})

    assert health_client(app_factory).get(HEALTH).json()["status"] == "degraded"


def test_health_degraded_on_consecutive_failures(app_factory: AppFactory, data_dir: Path) -> None:
    write_status(data_dir, overrides={"arrival:덕현초교": {"consecutive_failures": 3}})

    assert health_client(app_factory).get(HEALTH).json()["status"] == "degraded"


def test_health_degraded_when_status_file_is_from_yesterday(
    app_factory: AppFactory, data_dir: Path
) -> None:
    write_status(data_dir, now=FIXED_NOW - timedelta(days=1))

    assert health_client(app_factory).get(HEALTH).json()["status"] == "degraded"


def test_health_degraded_when_status_file_is_broken(
    app_factory: AppFactory, data_dir: Path
) -> None:
    (data_dir / STATUS_FILENAME).write_text("{broken", encoding="utf-8")

    body = health_client(app_factory).get(HEALTH).json()

    assert body["status"] == "degraded"
    assert body["lastSuccessAt"] is None


def test_health_right_after_window_start_is_ok(app_factory: AppFactory, data_dir: Path) -> None:
    # 05:30:05 에는 아직 성공 기록이 없어도 창 시작부터 주기 × 3 안이면 ok
    just_started = datetime(2026, 10, 7, 5, 30, 5, tzinfo=KST)
    status = {"date": "2026-10-07", "last_success_at": None, "apis": {}, "targets": {}}
    (data_dir / STATUS_FILENAME).write_text(json.dumps(status), encoding="utf-8")

    body = health_client(app_factory, now=just_started).get(HEALTH).json()

    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /health/detail
# ---------------------------------------------------------------------------
def test_health_detail_with_token(app_factory: AppFactory, data_dir: Path) -> None:
    write_status(data_dir)

    response = health_client(app_factory).get(DETAIL, headers={"X-Health-Token": HEALTH_TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    collector = body["collector"]
    assert collector["inWindow"] is True
    assert collector["lastSuccessAt"] == "2026-10-07T07:31:15+09:00"
    assert collector["today"] == {
        "date": "2026-10-07",
        "isHoliday": False,
        "calls": {"location": 1200, "arrival": 300},
        "failures": {"location": 4, "arrival": 1},
        "quotaExceeded": True,
        "throttled": True,
    }
    names = {t["name"] for t in collector["targets"]}
    assert {"G1300", "1306", "덕현초교"} <= names
    g1300 = next(t for t in collector["targets"] if t["name"] == "G1300")
    assert g1300 == {
        "name": "G1300",
        "intervalSec": 10,
        "calls": 100,
        "failures": 2,
        "empty": 3,
        "skippedCycles": 1,
        "lastSuccessAt": "2026-10-07T07:31:15+09:00",
    }


def test_health_detail_without_status_file(app_factory: AppFactory) -> None:
    response = health_client(app_factory).get(DETAIL, headers={"X-Health-Token": HEALTH_TOKEN})

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "idle"
    assert body["collector"]["targets"] == []
    assert body["collector"]["today"]["calls"] == {"location": 0, "arrival": 0}


@pytest.mark.parametrize(
    "headers",
    [
        {},
        {"X-Health-Token": "wrong"},
        {"X-Health-Token": ""},
        {"X-Health-Token": HEALTH_TOKEN + "x"},
    ],
)
def test_health_detail_wrong_or_missing_token_is_404(
    app_factory: AppFactory, headers: dict[str, str]
) -> None:
    client = health_client(app_factory)

    response = client.get(DETAIL, headers=headers)

    assert_error(response, 404, "NOT_FOUND")
    # 경로가 없는 것과 같은 응답이어야 존재를 드러내지 않는다.
    assert response.json() == client.get(f"{API}/no-such-path").json()


@pytest.mark.parametrize("headers", [{}, {"X-Health-Token": ""}, {"X-Health-Token": "anything"}])
def test_health_detail_is_404_when_server_token_unset(
    app_factory: AppFactory, headers: dict[str, str]
) -> None:
    client = health_client(app_factory, health_detail_token="")

    assert_error(client.get(DETAIL, headers=headers), 404, "NOT_FOUND")


def test_health_detail_short_server_token_counts_as_unset(app_factory: AppFactory) -> None:
    short_token = "x" * 31
    client = health_client(app_factory, health_detail_token=short_token)

    response = client.get(DETAIL, headers={"X-Health-Token": short_token})

    assert_error(response, 404, "NOT_FOUND")


def test_health_detail_is_hidden_from_openapi(app_factory: AppFactory) -> None:
    schema = health_client(app_factory).get("/openapi.json").json()

    assert "/api/v1/health/detail" not in schema["paths"]
    assert "/api/v1/health" in schema["paths"]
