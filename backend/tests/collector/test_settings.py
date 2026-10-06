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
    RAW_POLL_DB_SAVE_ENABLED,
    REPO_ROOT,
    YANGJU_ROUTES,
    ApiQuota,
    CallLimits,
    CollectTarget,
    call_timeout_sec,
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


def test_route_catalog_and_intervals() -> None:
    # 사용자 결정(2026-10-06): 양주시 관할 직행좌석형 11개 수집. G1300 10초, 1306 30초,
    # 나머지 40초, 도착(덕현초교) 30초. 라벨 대상은 G1300·1306.
    collected = {r.route_name: (r.route_id, r.interval_sec) for r in COLLECT_TARGET.collect_routes}
    assert collected == {
        "G1300": ("235000092", 10),
        "1306": ("235000123", 30),
        "1100": ("235000085", 40),
        "1101": ("235000115", 40),
        "1304": ("235000118", 40),
        "1407": ("235000131", 40),
        "8300": ("235000120", 40),
        "8906": ("235000103", 40),
        "G1200": ("235000104", 40),
        "P9601(출근)": ("233000371", 40),
        "P9602(출근)": ("233000373", 40),
        "P9603(출근)": ("235000127", 40),
    }
    not_collected = {r.route_name: r.route_id for r in YANGJU_ROUTES if not r.collect}
    assert not_collected == {
        "G1300N": "235000116",
        "P9601(퇴근)": "233000372",
        "P9602(퇴근)": "233000374",
        "P9603(퇴근)": "235000128",
        "3800": "218000151",
        "8109": "234001236",
    }
    names = [r.route_name for r in YANGJU_ROUTES]
    assert len(names) == len(set(names))
    assert [r.route_name for r in YANGJU_ROUTES if r.is_night] == ["G1300N"]
    reserved = [r.route_name for r in YANGJU_ROUTES if r.is_reserved and r.collect]
    assert reserved == ["P9601(출근)", "P9602(출근)", "P9603(출근)"]
    # 라벨 대상(routes)은 목록에서 label_target 인 노선 그대로다. discover·라벨 모듈이 쓴다.
    assert COLLECT_TARGET.routes == tuple(r for r in YANGJU_ROUTES if r.label_target)
    assert [r.route_name for r in COLLECT_TARGET.routes] == ["G1300", "1306"]
    assert COLLECT_TARGET.arrival_interval_sec == 30
    for route in COLLECT_TARGET.collect_routes:
        validate_trial_interval(route.interval_sec)
    validate_trial_interval(COLLECT_TARGET.arrival_interval_sec)
    assert missing_target_ids(COLLECT_TARGET) == []


def test_planned_daily_calls_6560_and_570() -> None:
    # 05:30~10:15 = 17,100초, 시작 포함·끝 제외:
    # G1300 1,710 + 1306 570 + 40초 노선 10개 × 428 = 6,560 ≤ 9,000, 도착 570 ≤ 950.
    assert planned_daily_calls() == {"buslocationservice": 6560, "busarrivalservice": 570}
    assert plan_problems() == []
    intervals = target_intervals()
    assert intervals["location:G1300"] == 10
    assert intervals["location:1306"] == 30
    assert intervals["arrival:덕현초교"] == 30
    assert len(intervals) == 13 and "location:G1300N" not in intervals


def test_planned_daily_calls_counts_boundaries_in_window() -> None:
    # 05:30 이상 10:15 미만: 10초 1,710개, 30초 570개, 40초 428개, 60초 285개.
    def only(name: str, interval_sec: int) -> CollectTarget:
        route = next(r for r in YANGJU_ROUTES if r.route_name == name)
        return replace(
            COLLECT_TARGET,
            route_catalog=(replace(route, interval_sec=interval_sec),),
            board_station_id=None,
        )

    assert planned_daily_calls(only("G1300", 10)) == {"buslocationservice": 1710}
    assert planned_daily_calls(only("1306", 30)) == {"buslocationservice": 570}
    assert planned_daily_calls(only("1100", 40)) == {"buslocationservice": 428}
    assert planned_daily_calls(only("1100", 60)) == {"buslocationservice": 285}
    bad = replace(COLLECT_TARGET, arrival_interval_sec=75)  # 범위(10~60초) 밖
    assert any("getBusArrivalListv2" in p for p in plan_problems(bad))
    duplicated = replace(
        COLLECT_TARGET, route_catalog=(*COLLECT_TARGET.route_catalog, YANGJU_ROUTES[0])
    )
    assert any("겹친다" in p for p in plan_problems(duplicated))


def test_plan_problems_uses_each_api_planned_max() -> None:
    heavy_location = replace(
        COLLECT_TARGET,
        route_catalog=tuple(replace(r, interval_sec=10) for r in COLLECT_TARGET.route_catalog),
    )
    problems = plan_problems(heavy_location)
    assert (
        len(problems) == 1
        and "buslocationservice 하루 예상 20520회 > 계획 상한 9000회" in (problems[0])
    )
    heavy_arrival = replace(COLLECT_TARGET, arrival_interval_sec=20)  # 855 ≤ 950
    assert plan_problems(heavy_arrival) == []
    too_heavy_arrival = replace(COLLECT_TARGET, arrival_interval_sec=15)  # 1,140 > 950
    assert any("busarrivalservice" in p for p in plan_problems(too_heavy_arrival))
    # 계획 상한을 작게 주입하면 지금 설정도 거부한다.
    small = CallLimits(location=ApiQuota(daily_limit=10_000, safe_limit=9_800, planned_max=6_559))
    assert any("buslocationservice" in p for p in plan_problems(COLLECT_TARGET, small))


def test_call_timeout_is_shorter_than_interval() -> None:
    assert call_timeout_sec(10) == 8.0
    assert call_timeout_sec(30) == 10.0
    assert call_timeout_sec(40) == 10.0
    assert all(
        call_timeout_sec(r.interval_sec) < r.interval_sec for r in COLLECT_TARGET.collect_routes
    )


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
