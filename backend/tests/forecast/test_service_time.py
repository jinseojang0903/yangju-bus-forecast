from datetime import datetime

import pytest

from app.core.settings import KST
from app.forecast.service_time import (
    in_forecast_hours,
    is_stale,
    next_forecast_start_at,
    next_refresh_at,
    service_state,
)


def at(month: int, day: int, hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, month, day, hour, minute, second, tzinfo=KST)


# 2026-10-07 수, 10-08 목, 10-09 금(설정 공휴일), 10-10 토, 10-12 월
WED_0545 = at(10, 7, 5, 45)
THU_0545 = at(10, 8, 5, 45)
MON_0545 = at(10, 12, 5, 45)


# ---------------------------------------------------------------------------
# service_state (계약 2.2절)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (at(10, 7, 5, 29, 59), ("outside_collection", WED_0545)),
        (at(10, 7, 5, 30), ("outside_forecast_hours", WED_0545)),
        (at(10, 7, 5, 44, 59), ("outside_forecast_hours", WED_0545)),
        (at(10, 7, 5, 45), ("in_service", None)),
        (at(10, 7, 7, 31, 20), ("in_service", None)),
        (at(10, 7, 8, 59, 59), ("in_service", None)),
        (at(10, 7, 9, 0), ("outside_forecast_hours", THU_0545)),
        (at(10, 7, 10, 14, 59), ("outside_forecast_hours", THU_0545)),
        (at(10, 7, 10, 15), ("outside_collection", THU_0545)),
        (at(10, 7, 23, 0), ("outside_collection", THU_0545)),
    ],
)
def test_service_state_boundaries(now: datetime, expected: tuple[str, datetime | None]) -> None:
    assert service_state(now) == expected


def test_holiday_is_outside_collection_and_skipped() -> None:
    # 10-09(설정 공휴일) 아침은 수집 시간이어도 예보하지 않는다
    assert service_state(at(10, 9, 7, 0)) == ("outside_collection", MON_0545)


def test_next_start_skips_holiday_and_weekend() -> None:
    assert service_state(at(10, 8, 10, 20)) == ("outside_collection", MON_0545)
    assert next_forecast_start_at(at(10, 10, 7, 0)) == MON_0545


def test_next_start_is_inclusive() -> None:
    assert next_forecast_start_at(WED_0545) == WED_0545


def test_next_start_serializes_with_kst_offset() -> None:
    assert next_forecast_start_at(at(10, 7, 10, 20)).isoformat() == "2026-10-08T05:45:00+09:00"


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        service_state(datetime(2026, 10, 7, 7, 0))


# ---------------------------------------------------------------------------
# 예보 대상 시간대(06:00~08:59 도착)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("arrival", "expected"),
    [
        (at(10, 7, 5, 59, 59), False),
        (at(10, 7, 6, 0), True),
        (at(10, 7, 8, 59, 59), True),
        (at(10, 7, 9, 0), False),
    ],
)
def test_in_forecast_hours(arrival: datetime, expected: bool) -> None:
    assert in_forecast_hours(arrival) is expected


# ---------------------------------------------------------------------------
# next_refresh_at (계약 1.3절, 가장 짧은 주기 10초 + 3초)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("now", "data_updated_at", "expected"),
    [
        # 계약 9장 예시
        (at(10, 7, 7, 31, 20), at(10, 7, 7, 31, 10), at(10, 7, 7, 31, 33)),
        (at(10, 7, 7, 31, 21), at(10, 7, 7, 31, 10), at(10, 7, 7, 31, 33)),
        (at(10, 7, 7, 31, 29), None, at(10, 7, 7, 31, 33)),
        (at(10, 7, 7, 31, 30), None, at(10, 7, 7, 31, 43)),
        # 데이터 시각이 now 보다 늦으면(시계 차) 데이터 시각 다음 경계
        (at(10, 7, 7, 31, 20), at(10, 7, 7, 31, 41), at(10, 7, 7, 31, 53)),
    ],
)
def test_next_refresh_in_service(
    now: datetime, data_updated_at: datetime | None, expected: datetime
) -> None:
    assert next_refresh_at(now, data_updated_at) == expected


def test_next_refresh_outside_service_is_next_forecast_start() -> None:
    assert next_refresh_at(at(10, 7, 9, 30), at(10, 7, 9, 29, 50)) == THU_0545
    assert next_refresh_at(at(10, 9, 7, 0), None) == MON_0545


def test_next_refresh_custom_interval() -> None:
    assert next_refresh_at(at(10, 7, 7, 31, 20), None, interval_sec=30) == at(10, 7, 7, 31, 33)
    assert next_refresh_at(at(10, 7, 7, 31, 30), None, interval_sec=30) == at(10, 7, 7, 32, 3)


# ---------------------------------------------------------------------------
# is_stale (초기 60초)
# ---------------------------------------------------------------------------
def test_is_stale_boundaries() -> None:
    now = at(10, 7, 7, 31, 20)

    assert is_stale(now, at(10, 7, 7, 30, 20)) is False  # 정확히 60초
    assert is_stale(now, at(10, 7, 7, 30, 19)) is True
    assert is_stale(now, None) is True
