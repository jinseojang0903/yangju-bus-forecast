"""수집 창·공휴일·시계. 시각은 OS 시간대와 무관하게 KST 로 계산한다."""

import math
from datetime import date, datetime, timedelta
from functools import lru_cache
from typing import Protocol

import holidays

from app.core.settings import COLLECT_WINDOW, KST, TICK_GRACE_SEC


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


def ceil_to_interval(moment: datetime, interval_sec: int = COLLECT_WINDOW.interval_sec) -> datetime:
    """다음 주기 경계(분 경계면 초 0). 이미 경계 위면 그대로 둔다."""
    local = to_kst(moment)
    boundary = math.ceil(local.timestamp() / interval_sec) * interval_sec
    return datetime.fromtimestamp(boundary, KST)


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
    timestamp = local.timestamp()
    floor = math.floor(timestamp / interval_sec) * interval_sec
    passed = datetime.fromtimestamp(floor, KST)
    if timestamp - floor <= grace_sec and (last_tick is None or passed > last_tick):
        return passed
    tick = ceil_to_interval(local, interval_sec)
    if last_tick is not None and tick <= last_tick:
        tick = last_tick + timedelta(seconds=interval_sec)
    return tick
