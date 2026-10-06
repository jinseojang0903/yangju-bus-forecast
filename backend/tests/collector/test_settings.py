from dataclasses import replace
from datetime import time
from pathlib import Path

from app.collector.collector import missing_target_ids, plan_calls, plan_problems
from app.collector.db import build_raw_poll_sink
from app.collector.schedule import validate_trial_interval
from app.core.settings import (
    BACKEND_DIR,
    COLLECT_TARGET,
    COLLECT_WINDOW,
    DEFAULT_COLLECT_DATA_DIR,
    DEFAULT_LOG_DIR,
    ENV_FILE,
    FORECAST_RULES,
    KST,
    LLM_LIMITS,
    PLANNED_DAILY_MAX,
    RAW_POLL_DB_SAVE_ENABLED,
    REPO_ROOT,
    planned_daily_calls,
    target_intervals,
)
from tests.collector.helpers import TEST_TARGET, make_settings


def test_paths_do_not_depend_on_cwd() -> None:
    assert BACKEND_DIR.name == "backend"
    assert ENV_FILE == BACKEND_DIR / ".env"
    assert DEFAULT_COLLECT_DATA_DIR == REPO_ROOT / "data" / "collected"
    assert DEFAULT_LOG_DIR == REPO_ROOT / "data" / "logs"


def test_data_dir_default_and_override(tmp_path: Path) -> None:
    assert make_settings().data_dir == DEFAULT_COLLECT_DATA_DIR
    assert make_settings().log_dir == DEFAULT_LOG_DIR
    moved = make_settings(collect_data_dir=str(tmp_path))
    assert moved.data_dir == tmp_path
    assert moved.log_dir == tmp_path / "logs"


def test_fixed_constants() -> None:
    assert str(KST) == "Asia/Seoul"
    assert COLLECT_WINDOW.weekdays == (0, 1, 2, 3, 4)
    assert COLLECT_WINDOW.start == time(5, 30)  # 사용자 결정(2026-10-06)
    assert COLLECT_WINDOW.end == time(10, 15)
    assert COLLECT_WINDOW.interval_sec == 60
    assert [r.route_name for r in COLLECT_TARGET.routes] == ["G1300", "1306"]
    assert [r.alight_station_name for r in COLLECT_TARGET.routes] == ["잠실광역환승센터", "잠실역"]
    assert COLLECT_TARGET.board_station_name == "덕현초교"
    assert COLLECT_TARGET.region_keyword == "양주"
    assert COLLECT_TARGET.walk_minutes_allowed == 0
    assert COLLECT_TARGET.transfers_allowed == 0
    assert FORECAST_RULES.lead_times_min == (5, 10, 15)
    assert (FORECAST_RULES.seat_tolerance, FORECAST_RULES.headway_tolerance_min) == (2, 3)
    assert (FORECAST_RULES.seat_distance_divisor, FORECAST_RULES.headway_distance_divisor) == (2, 3)
    assert (FORECAST_RULES.max_cases, FORECAST_RULES.min_cases_to_show) == (30, 20)
    assert (FORECAST_RULES.risk_high_min, FORECAST_RULES.risk_low_below) == (0.7, 0.3)
    assert (LLM_LIMITS.timeout_sec, LLM_LIMITS.daily_call_limit) == (3.0, 300)


def test_collect_intervals_and_planned_daily_calls() -> None:
    # 사용자 결정(2026-10-06): G1300 30초, 1306 60초, 도착 60초, 창 05:30~10:15
    assert [r.interval_sec for r in COLLECT_TARGET.routes] == [30, 60]
    assert COLLECT_TARGET.arrival_interval_sec == 60
    for route in COLLECT_TARGET.routes:
        validate_trial_interval(route.interval_sec)
    validate_trial_interval(COLLECT_TARGET.arrival_interval_sec)
    assert planned_daily_calls() == {"buslocationservice": 855, "busarrivalservice": 285}
    assert PLANNED_DAILY_MAX == 950
    assert all(count <= PLANNED_DAILY_MAX for count in planned_daily_calls().values())
    assert plan_problems() == []
    assert target_intervals() == {"location:G1300": 30, "location:1306": 60, "arrival:덕현초교": 60}


def test_planned_daily_calls_counts_boundaries_in_window() -> None:
    # 05:30 이상 10:15 미만: 30초 경계 570개, 60초 경계 285개.
    only_g1300 = replace(COLLECT_TARGET, routes=COLLECT_TARGET.routes[:1], board_station_id=None)
    assert planned_daily_calls(only_g1300) == {"buslocationservice": 570}
    heavy = replace(
        COLLECT_TARGET, routes=tuple(replace(r, interval_sec=15) for r in COLLECT_TARGET.routes)
    )
    assert planned_daily_calls(heavy)["buslocationservice"] == 1140 * 2
    assert any("buslocationservice" in p for p in plan_problems(heavy))
    bad = replace(COLLECT_TARGET, arrival_interval_sec=75)  # 범위(10~60초) 밖
    assert any("getBusArrivalListv2" in p for p in plan_problems(bad))


def test_db_save_is_off_even_with_database_url() -> None:
    assert RAW_POLL_DB_SAVE_ENABLED is False
    settings = make_settings(database_url="postgresql://user:pw@localhost:5432/db")
    assert build_raw_poll_sink(settings) is None


def test_plan_and_missing_ids() -> None:
    planned = plan_calls(TEST_TARGET)
    assert [p.endpoint.api for p in planned] == [
        "getBusLocationListv2",
        "getBusLocationListv2",
        "getBusArrivalListv2",
    ]
    assert missing_target_ids(TEST_TARGET) == []
    assert [p.params for p in planned] == [
        {"routeId": "900000001"},
        {"routeId": "900000002"},
        {"stationId": "900000105"},
    ]


def test_unfilled_target_reports_missing_ids() -> None:
    # 실제 설정은 discover 결과를 메인이 채우기 전까지 비어 있다.
    if all(r.route_id for r in COLLECT_TARGET.routes) and COLLECT_TARGET.board_station_id:
        return
    assert missing_target_ids(COLLECT_TARGET)
