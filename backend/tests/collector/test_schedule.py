from datetime import UTC, date, datetime, timedelta

import pytest

from app.collector.gbis import CallResult
from app.collector.schedule import (
    ceil_to_interval,
    floor_to_interval,
    is_holiday,
    is_in_window,
    is_today_window_over,
    is_weekday,
    next_tick,
    next_window_start,
    validate_trial_interval,
)
from app.collector.storage import build_record
from tests.collector.helpers import kst

# 2026-10-07 수요일, 2026-10-09 금요일(한글날), 2026-10-10 토요일, 2026-10-11 일요일


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (kst(2026, 10, 7, 4, 59), False),
        (kst(2026, 10, 7, 4, 59, 59), False),
        (kst(2026, 10, 7, 5, 0), True),
        (kst(2026, 10, 7, 9, 59), True),
        (kst(2026, 10, 7, 9, 59, 59), True),
        (kst(2026, 10, 7, 10, 0), False),
        (kst(2026, 10, 10, 6, 0), False),  # 토
        (kst(2026, 10, 11, 6, 0), False),  # 일
        (kst(2026, 10, 9, 6, 0), True),  # 평일 공휴일은 수집하고 표시만 한다
    ],
)
def test_collect_window(moment: datetime, expected: bool) -> None:
    assert is_in_window(moment) is expected


def test_window_uses_kst_regardless_of_input_timezone() -> None:
    # UTC 2026-10-06 20:00 = KST 2026-10-07 05:00
    assert is_in_window(datetime(2026, 10, 6, 20, 0, tzinfo=UTC)) is True
    assert is_in_window(datetime(2026, 10, 6, 19, 59, tzinfo=UTC)) is False


def test_naive_datetime_is_rejected() -> None:
    with pytest.raises(ValueError):
        is_in_window(datetime(2026, 10, 7, 6, 0))


def test_holiday_flags() -> None:
    assert is_holiday(date(2026, 10, 9)) is True  # 한글날
    assert is_weekday(date(2026, 10, 9)) is True
    assert is_holiday(date(2026, 10, 7)) is False
    assert is_weekday(date(2026, 10, 10)) is False


def test_record_marks_holiday_and_weekday() -> None:
    result = CallResult(
        api="getBusLocationListv2",
        service="buslocationservice",
        params={"routeId": "1", "format": "json"},
        http_status=200,
        elapsed_ms=10,
        ok=True,
        result_code="0",
        result_message="ok",
        error=None,
        body={"response": {}},
    )
    record = build_record(
        collected_at=kst(2026, 10, 9, 6, 0), result=result, mode="run", interval_sec=60
    )
    assert record["is_holiday"] is True and record["is_weekday"] is True
    assert record["collected_at"] == "2026-10-09T06:00:00.000+09:00"
    assert list(record) == [
        "collected_at",
        "api",
        "params",
        "http_status",
        "elapsed_ms",
        "ok",
        "result_code",
        "result_message",
        "error",
        "is_weekday",
        "is_holiday",
        "mode",
        "interval_sec",
        "body",
    ]
    assert record["interval_sec"] == 60


def test_next_window_start() -> None:
    assert next_window_start(kst(2026, 10, 7, 4, 0)) == kst(2026, 10, 7, 5, 0)
    assert next_window_start(kst(2026, 10, 7, 6, 30)) == kst(2026, 10, 7, 6, 30)
    assert next_window_start(kst(2026, 10, 7, 10, 0)) == kst(2026, 10, 8, 5, 0)
    # 금 10:00 이후 → 월 05:00
    assert next_window_start(kst(2026, 10, 9, 10, 0)) == kst(2026, 10, 12, 5, 0)


def test_today_window_over() -> None:
    assert is_today_window_over(kst(2026, 10, 7, 4, 55)) is False
    assert is_today_window_over(kst(2026, 10, 7, 10, 0)) is True
    assert is_today_window_over(kst(2026, 10, 10, 4, 55)) is True


def test_next_tick_accepts_slightly_late_wakeup() -> None:
    late = kst(2026, 10, 7, 5, 0) + timedelta(milliseconds=3)
    # 처음 깨어났을 때: 지나간 05:00 경계를 쓴다
    assert next_tick(late, None) == kst(2026, 10, 7, 5, 0)
    # 05:00 에 이미 호출했으면 다음 경계
    assert next_tick(late, kst(2026, 10, 7, 5, 0)) == kst(2026, 10, 7, 5, 1)
    # 여유(5초)를 넘겨 늦으면 다음 경계
    assert next_tick(kst(2026, 10, 7, 5, 0, 6), None) == kst(2026, 10, 7, 5, 1)
    # 정확히 경계 위
    assert next_tick(kst(2026, 10, 7, 5, 0), None) == kst(2026, 10, 7, 5, 0)


def test_ceil_to_minute_boundary() -> None:
    assert ceil_to_interval(kst(2026, 10, 7, 5, 0, 0)) == kst(2026, 10, 7, 5, 0)
    assert ceil_to_interval(kst(2026, 10, 7, 5, 0, 1)) == kst(2026, 10, 7, 5, 1)
    assert ceil_to_interval(kst(2026, 10, 7, 4, 59, 30)) == kst(2026, 10, 7, 5, 0)


def test_40_second_boundaries_are_multiples_from_kst_midnight() -> None:
    assert ceil_to_interval(kst(2026, 10, 6, 10, 20, 1), 40) == kst(2026, 10, 6, 10, 20, 40)
    assert ceil_to_interval(kst(2026, 10, 6, 10, 20, 41), 40) == kst(2026, 10, 6, 10, 21, 20)
    assert floor_to_interval(kst(2026, 10, 6, 10, 21, 19), 40) == kst(2026, 10, 6, 10, 20, 40)
    # 자정 직전 → 다음 날 00:00:00 (86400 = 40 × 2160)
    assert ceil_to_interval(kst(2026, 10, 6, 23, 59, 21), 40) == kst(2026, 10, 7, 0, 0)
    late = kst(2026, 10, 6, 10, 20, 40) + timedelta(milliseconds=3)
    assert next_tick(late, kst(2026, 10, 6, 10, 20), 40) == kst(2026, 10, 6, 10, 20, 40)
    assert next_tick(late, kst(2026, 10, 6, 10, 20, 40), 40) == kst(2026, 10, 6, 10, 21, 20)


@pytest.mark.parametrize("value", [20, 30, 40, 45, 48, 60])
def test_trial_interval_accepts_divisors_in_range(value: int) -> None:
    validate_trial_interval(value)


@pytest.mark.parametrize("value", [19, 35, 61, 0, -40, 120])
def test_trial_interval_rejects_out_of_range_or_non_divisor(value: int) -> None:
    with pytest.raises(ValueError):
        validate_trial_interval(value)
