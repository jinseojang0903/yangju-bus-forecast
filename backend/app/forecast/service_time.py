"""서비스 시간·다음 조회 시각·정보 오래됨(계약 1.3절, 2.2절).

시각은 모두 KST 로 계산한다. 입력 datetime 은 시간대가 있어야 한다.
"""

from datetime import date, datetime, time, timedelta

from app.core.settings import (
    COLLECT_TARGET,
    COLLECT_WINDOW,
    KST,
    NEXT_REFRESH_MARGIN_SEC,
    SERVICE_HOURS,
    STALE_AFTER_SEC,
    CollectWindow,
    ServiceHours,
    target_intervals,
)
from app.schemas.common import ServiceState

# 공휴일·주말이 이어져도 이 안에는 다음 평일이 있다.
_MAX_DAYS_TO_NEXT_SERVICE = 31


def _to_kst(moment: datetime) -> datetime:
    if moment.tzinfo is None:
        raise ValueError("시간대가 없는 datetime 은 받지 않는다")
    return moment.astimezone(KST)


def is_service_day(
    day: date,
    window: CollectWindow = COLLECT_WINDOW,
    hours: ServiceHours = SERVICE_HOURS,
) -> bool:
    """예보하는 날: 수집 요일(평일)이고 설정의 공휴일 목록에 없다."""
    return day.weekday() in window.weekdays and day not in hours.holidays


def in_forecast_hours(station_arrival_at: datetime, hours: ServiceHours = SERVICE_HOURS) -> bool:
    """내 정류장 도착이 예보 대상 시간대(06:00 이상 09:00 미만, KST)이면 True."""
    arrival = _to_kst(station_arrival_at).time()
    return hours.forecast_arrival_start <= arrival < hours.forecast_arrival_end


def next_forecast_start_at(
    now: datetime,
    window: CollectWindow = COLLECT_WINDOW,
    hours: ServiceHours = SERVICE_HOURS,
) -> datetime:
    """now 이후(같은 시각 포함) 처음 오는 '예보하는 날의 05:45'(KST)."""
    local = _to_kst(now)
    for offset in range(_MAX_DAYS_TO_NEXT_SERVICE + 1):
        day = local.date() + timedelta(days=offset)
        if not is_service_day(day, window, hours):
            continue
        start = datetime.combine(day, hours.forecast_start, tzinfo=KST)
        if start >= local:
            return start
    raise RuntimeError("31일 안에 예보하는 날이 없다. 요일·공휴일 설정을 확인한다")


def service_state(
    now: datetime,
    window: CollectWindow = COLLECT_WINDOW,
    hours: ServiceHours = SERVICE_HOURS,
) -> tuple[ServiceState, datetime | None]:
    """(service.state, nextForecastStartAt). in_service 이면 두 번째 값은 None.

    - outside_collection: 수집 시간(평일 05:30 이상 10:15 미만) 밖이거나 설정 공휴일
    - outside_forecast_hours: 수집 시간 안이지만 예보 시간(05:45 이상 09:00 미만) 밖
    - in_service: 그 밖

    예보 시간은 '06:00~08:59 도착 버스의 15분 예보가 나오는 05:45부터 마지막 대상 버스가
    도착하는 09:00 전까지'로 본다. 버스 목록 없이 시각만으로 판정한다.
    """
    local = _to_kst(now)
    today = local.date()
    # collector.schedule.is_in_window 과 겹치지만 공휴일 기준이 달라 따로 판정한다
    # (수집기는 공휴일에도 수집하고, 예보는 설정 공휴일 목록에서 쉰다).
    collect_start = datetime.combine(today, window.start, tzinfo=KST)
    collect_end = datetime.combine(today, window.end, tzinfo=KST)
    if not is_service_day(today, window, hours) or not collect_start <= local < collect_end:
        return "outside_collection", next_forecast_start_at(local, window, hours)
    forecast_start = datetime.combine(today, hours.forecast_start, tzinfo=KST)
    forecast_end = datetime.combine(today, hours.forecast_arrival_end, tzinfo=KST)
    if not forecast_start <= local < forecast_end:
        return "outside_forecast_hours", next_forecast_start_at(local, window, hours)
    return "in_service", None


def shortest_collect_interval_sec() -> int:
    """수집 대상 주기 중 가장 짧은 값(초). 지금 설정이면 G1300 위치 10초."""
    return min(target_intervals(COLLECT_TARGET).values())


def next_refresh_at(
    now: datetime,
    data_updated_at: datetime | None,
    *,
    interval_sec: int | None = None,
    margin_sec: int = NEXT_REFRESH_MARGIN_SEC,
) -> datetime:
    """다음 조회 권장 시각(계약 1.3절).

    - 서비스 시간 밖(service_state 가 in_service 가 아님): 다음 예보 시작 시각.
    - 그 밖: max(now, data_updated_at) 보다 뒤인 첫 주기 경계 + margin_sec.
      주기는 가장 짧은 수집 대상 주기이고, 경계는 KST 자정 기준 주기의 배수다.
      예: now 07:31:20, 주기 10초 → 07:31:30 + 3초 = 07:31:33.
    """
    state, next_start = service_state(now)
    if state != "in_service" and next_start is not None:
        return next_start
    interval = interval_sec or shortest_collect_interval_sec()
    base = _to_kst(now)
    if data_updated_at is not None:
        base = max(base, _to_kst(data_updated_at))
    midnight = datetime.combine(base.date(), time(0, 0), tzinfo=KST)
    elapsed = (base - midnight).total_seconds()
    next_boundary = midnight + timedelta(seconds=(int(elapsed // interval) + 1) * interval)
    return next_boundary + timedelta(seconds=margin_sec)


def is_stale(
    now: datetime, data_updated_at: datetime | None, stale_after_sec: int = STALE_AFTER_SEC
) -> bool:
    """수집 데이터가 오래됐으면 True. 갱신 시각이 없으면 오래된 것으로 본다."""
    if data_updated_at is None:
        return True
    return (_to_kst(now) - _to_kst(data_updated_at)).total_seconds() > stale_after_sec
