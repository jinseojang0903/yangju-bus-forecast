"""수집 상태(계약 4.1·4.2절). 수집기 상태 파일만 읽고 GBIS 를 부르지 않는다.

status 판정
- 상태 파일이 없다 → idle, lastSuccessAt null
- 수집 시간(평일 05:30 이상 10:15 미만) 밖 → idle
- 수집 시간 안: 상태 파일이 깨졌거나 오늘 것이 아니거나, 어느 대상이든 마지막 성공이
  주기 × HEALTH_STALE_INTERVAL_MULTIPLIER 보다 오래됐거나(창 시작 직후는 창 시작 시각부터 잼),
  연속 실패가 HEALTH_DEGRADED_CONSECUTIVE_FAILURES 이상이면 degraded, 아니면 ok
"""

from datetime import datetime
from typing import Any

from app.collector.schedule import is_holiday, is_in_window, to_kst, window_bounds
from app.core.settings import (
    COLLECT_TARGET,
    GBIS_BUS_ARRIVAL,
    GBIS_BUS_LOCATION,
    HEALTH_DEGRADED_CONSECUTIVE_FAILURES,
    HEALTH_STALE_INTERVAL_MULTIPLIER,
    target_intervals,
)
from app.repositories.collector_status import CollectorStatusRead, CollectorStatusRepository
from app.schemas.common import HealthStatus
from app.schemas.health import (
    ApiCounts,
    CollectorDetail,
    CollectorTarget,
    CollectorToday,
    HealthDetailResponse,
    HealthResponse,
)


def _as_int(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) else 0


def _as_time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return to_kst(parsed) if parsed.tzinfo is not None else None


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _is_today(data: dict[str, Any] | None, now: datetime) -> bool:
    return data is not None and data.get("date") == to_kst(now).date().isoformat()


def _today_targets(data: dict[str, Any] | None, now: datetime) -> dict[str, dict[str, Any]]:
    if not _is_today(data, now):
        return {}
    targets = _dict((data or {}).get("targets"))
    return {key: _dict(value) for key, value in targets.items()}


def _is_target_degraded(counters: dict[str, Any], interval_sec: int, now: datetime) -> bool:
    if _as_int(counters.get("consecutive_failures")) >= HEALTH_DEGRADED_CONSECUTIVE_FAILURES:
        return True
    window_start, _ = window_bounds(to_kst(now).date())
    last_success = _as_time(counters.get("last_success_at"))
    reference = max(last_success, window_start) if last_success else window_start
    return (now - reference).total_seconds() > interval_sec * HEALTH_STALE_INTERVAL_MULTIPLIER


def evaluate_status(read: CollectorStatusRead, now: datetime) -> HealthStatus:
    if read.state == "missing" or not is_in_window(now):
        return "idle"
    if read.state == "unreadable" or not _is_today(read.data, now):
        return "degraded"
    saved = _today_targets(read.data, now)
    # 설정의 수집 대상 기준. 상태 파일에 아직 없는 대상(한 번도 안 부름)도 본다.
    for key, interval_sec in target_intervals(COLLECT_TARGET).items():
        counters = saved.get(key, {})
        interval = _as_int(counters.get("interval_sec")) or interval_sec
        if _is_target_degraded(counters, interval, now):
            return "degraded"
    return "ok"


def _last_success_at(read: CollectorStatusRead) -> datetime | None:
    return _as_time(read.data.get("last_success_at")) if read.data else None


class HealthService:
    def __init__(self, repository: CollectorStatusRepository) -> None:
        self._repository = repository

    def get_health(self, now: datetime) -> HealthResponse:
        read = self._repository.read()
        return HealthResponse(
            status=evaluate_status(read, now),
            now=to_kst(now),
            last_success_at=_last_success_at(read),
        )

    def get_health_detail(self, now: datetime) -> HealthDetailResponse:
        read = self._repository.read()
        local = to_kst(now)
        apis = _dict(read.data.get("apis")) if _is_today(read.data, now) and read.data else {}
        location = _dict(apis.get(GBIS_BUS_LOCATION.service))
        arrival = _dict(apis.get(GBIS_BUS_ARRIVAL.service))
        targets = [
            CollectorTarget(
                name=key.split(":", 1)[-1],
                interval_sec=_as_int(counters.get("interval_sec")),
                calls=_as_int(counters.get("calls")),
                failures=_as_int(counters.get("failure")),
                empty=_as_int(counters.get("empty")),
                skipped_cycles=_as_int(counters.get("skipped_cycles")),
                last_success_at=_as_time(counters.get("last_success_at")),
            )
            for key, counters in _today_targets(read.data, now).items()
        ]
        return HealthDetailResponse(
            status=evaluate_status(read, now),
            now=local,
            collector=CollectorDetail(
                in_window=is_in_window(local),
                last_success_at=_last_success_at(read),
                today=CollectorToday(
                    date=local.date().isoformat(),
                    is_holiday=is_holiday(local.date()),
                    calls=ApiCounts(
                        location=_as_int(location.get("calls")),
                        arrival=_as_int(arrival.get("calls")),
                    ),
                    failures=ApiCounts(
                        location=_as_int(location.get("failure")),
                        arrival=_as_int(arrival.get("failure")),
                    ),
                    quota_exceeded=any(
                        _as_int(api.get("quota_exceeded")) > 0 for api in (location, arrival)
                    ),
                    throttled=any(api.get("throttled") is True for api in (location, arrival)),
                ),
                targets=targets,
            ),
        )
