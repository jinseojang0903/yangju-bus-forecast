"""수집 창·공휴일·시계. 시각은 OS 시간대와 무관하게 KST 로 계산한다."""

import math
from datetime import date, datetime, time, timedelta
from functools import lru_cache
from typing import Protocol

import holidays

from app.core.settings import (
    COLLECT_WINDOW,
    KST,
    TICK_GRACE_SEC,
    TRIAL_INTERVAL_MAX_SEC,
    TRIAL_INTERVAL_MIN_SEC,
)


class Clock(Protocol):
    def now(self) -> datetime:
        """시간대가 있는 현재 시각."""
        ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(KST)


def to_kst(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("시간대가 없는 datetime 은 받지 않는다")
    return moment.astimezone(KST)


def is_weekday(day: date) -> bool:
    return day.weekday() in COLLECT_WINDOW.weekdays


@lru_cache(maxsize=8)
def _korean_holidays(year: int) -> holidays.HolidayBase:
    return holidays.country_holidays("KR", years=year)


def is_holiday(day: date) -> bool:
    return day in _korean_holidays(day.year)


def window_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, COLLECT_WINDOW.start, tzinfo=KST)
    end = datetime.combine(day, COLLECT_WINDOW.end, tzinfo=KST)
    return start, end


def is_in_window(moment: datetime) -> bool:
    """평일 start 이상 end 미만이면 True.

    공휴일은 창에서 빼지 않는다(is_holiday 로 표시만 한다).
    """
    local = to_kst(moment)
    if not is_weekday(local.date()):
        return False
    start, end = window_bounds(local.date())
    return start <= local < end


def next_window_start(moment: datetime) -> datetime:
    """창 안이면 moment 그대로, 아니면 다음 창의 시작 시각."""
    local = to_kst(moment)
    for offset in range(0, 8):
        day = local.date() + timedelta(days=offset)
        if not is_weekday(day):
            continue
        start, end = window_bounds(day)
        if local < start:
            return start
        if local < end:
            return local
    raise RuntimeError("7일 안에 수집 창이 없다. COLLECT_WINDOW.weekdays 를 확인한다")


def is_today_window_over(moment: datetime) -> bool:
    """오늘 더 수집할 창이 없으면 True(주말, 또는 평일 창 종료 시각 이후)."""
    local = to_kst(moment)
    if not is_weekday(local.date()):
        return True
    _, end = window_bounds(local.date())
    return local >= end


SECONDS_PER_DAY = 86400


def _check_interval(interval_sec: int) -> None:
    # 하루가 간격으로 나누어떨어져야 KST 자정 기준 경계가 매일 같다.
    if interval_sec <= 0 or SECONDS_PER_DAY % interval_sec != 0:
        raise ValueError(f"interval_sec 는 86400 의 약수여야 한다: {interval_sec}")


def validate_trial_interval(interval_sec: int) -> None:
    """수집 간격(시운전 --interval-sec, 대상별 정식 주기 공통): 10~60초, 86400 의 약수.

    아니면 ValueError.
    """
    if not TRIAL_INTERVAL_MIN_SEC <= interval_sec <= TRIAL_INTERVAL_MAX_SEC:
        raise ValueError(f"간격은 {TRIAL_INTERVAL_MIN_SEC}~{TRIAL_INTERVAL_MAX_SEC}초여야 한다")
    if SECONDS_PER_DAY % interval_sec != 0:
        raise ValueError("간격은 86400(하루 초)을 나누어떨어지게 해야 한다")


def _day_start(local: datetime) -> datetime:
    return datetime.combine(local.date(), time(0, 0), tzinfo=KST)


def _offset_sec(local: datetime) -> float:
    """KST 자정부터 지난 초."""
    return (local - _day_start(local)).total_seconds()


def floor_to_interval(
    moment: datetime, interval_sec: int = COLLECT_WINDOW.interval_sec
) -> datetime:
    """지금 또는 바로 앞의 주기 경계. 경계는 KST 자정 기준 interval_sec 의 배수다."""
    _check_interval(interval_sec)
    local = to_kst(moment)
    steps = math.floor(_offset_sec(local) / interval_sec)
    return _day_start(local) + timedelta(seconds=steps * interval_sec)


def is_on_boundary(moment: datetime, interval_sec: int) -> bool:
    """moment 가 KST 자정 기준 interval_sec 배수 경계 위에 있으면 True."""
    return floor_to_interval(moment, interval_sec) == to_kst(moment)


def ceil_to_interval(moment: datetime, interval_sec: int = COLLECT_WINDOW.interval_sec) -> datetime:
    """다음 주기 경계(60초면 초 0). 이미 경계 위면 그대로 둔다.

    경계는 KST 자정 기준 interval_sec 의 배수다(40초면 10:20:00, 10:20:40, 10:21:20 …).
    """
    _check_interval(interval_sec)
    local = to_kst(moment)
    steps = math.ceil(_offset_sec(local) / interval_sec)
    # 자정 직전이면 다음 날 00:00:00 이 된다(86400 이 interval_sec 로 나누어떨어짐).
    return _day_start(local) + timedelta(seconds=steps * interval_sec)


def next_tick(
    moment: datetime,
    last_tick: datetime | None,
    interval_sec: int = COLLECT_WINDOW.interval_sec,
    grace_sec: float = TICK_GRACE_SEC,
) -> datetime:
    """이번에 호출할 주기 경계. 항상 last_tick 보다 뒤다.

    잠에서 경계보다 몇 ms 늦게 깨어나도 그 경계를 놓치지 않도록, 경계를 grace_sec 이내로
    지났고 그 경계에서 아직 호출하지 않았으면 그 경계를 돌려준다. 아니면 다음 경계.
    """
    local = to_kst(moment)
    passed = floor_to_interval(local, interval_sec)
    late_sec = (local - passed).total_seconds()
    if late_sec <= grace_sec and (last_tick is None or passed > last_tick):
        return passed
    tick = ceil_to_interval(local, interval_sec)
    if last_tick is not None and tick <= last_tick:
        tick = last_tick + timedelta(seconds=interval_sec)
    return tick
