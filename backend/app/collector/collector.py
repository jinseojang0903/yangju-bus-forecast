"""대상별 독립 작업자로 도는 수집기.

대상(위치 v2 × 노선, 도착 v2 × 정류장)마다 작업자 스레드가 하나씩 있다(settings.COLLECT_TARGET).
- 각 작업자는 자기 주기의 KST 자정 기준 경계에서 호출하고, 다른 작업자를 기다리지 않는다.
- 늦게 깨어남 허용(TICK_GRACE_SEC)과 늦은 주기 건너뛰기는 작업자마다 따로 적용하고,
  건너뛴 주기 수를 대상별로 센다(상태 파일 targets, 로그 WARNING cycles_skipped).
- HTTP 클라이언트는 대상마다 따로 둔다(httpx.Client 를 스레드 간에 공유하지 않는다).
  타임아웃은 그 대상의 주기보다 짧다(settings.call_timeout_sec: 10초 주기 → 8초).
- JSONL 쓰기·status.json 갱신·DB 저장은 잠금으로 직렬화한다. 상태 파일은 묶어 쓰기
  (여러 작업자의 저장 요청을 파일 쓰기 한 번으로)를 해 잠금 대기로 G1300 이 밀리지 않게 한다.
- 호출 수는 호출 전에 저장한다. API 별 안전 상한에 닿으면 그 API 의 모든 작업자가
  그날 호출을 멈춘다.
수집에 실패한 시간의 데이터는 다시 얻을 수 없으므로, 어떤 예외도 작업자를 멈추지 않게 하고 기록한다.
"""

import logging
import math
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from functools import partial
from pathlib import Path
from typing import Any, Protocol

from app.collector.db import RawPollSink
from app.collector.gbis import CallResult, GbisClient
from app.collector.redact import Redactor, describe_exception
from app.collector.schedule import (
    Clock,
    is_in_window,
    is_on_boundary,
    is_today_window_over,
    next_tick,
    next_window_start,
    to_kst,
    validate_trial_interval,
)
from app.collector.status import StatusStore, status_path
from app.collector.storage import append_jsonl, build_record, raw_poll_path
from app.core.settings import (
    CALL_LIMITS,
    COLLECT_TARGET,
    COLLECT_WINDOW,
    GBIS_BUS_ARRIVAL,
    GBIS_BUS_LOCATION,
    QUOTA_THROTTLE_AFTER_CONSECUTIVE,
    QUOTA_THROTTLE_PROBE_INTERVAL_SEC,
    WORKER_STALL_INTERVALS,
    WORKER_STALL_MIN_SEC,
    CallLimits,
    CollectTarget,
    GbisEndpoint,
    call_timeout_sec,
    planned_daily_calls,
)

logger = logging.getLogger(__name__)

MODE_RUN = "run"
MODE_ONCE = "once"
# 시운전. 평가·사례에서 이 mode 의 기록은 뺀다.
MODE_TRIAL = "trial"
# 이 시간(초)마다 INFO 로 누적 호출 수를 남긴다.
HEARTBEAT_EVERY_SEC = 1800
# 상태 파일·JSONL 잠금을 이보다 오래 기다리면 WARNING(G1300 이 밀리는지 보려고).
LOCK_WAIT_WARN_SEC = 0.5
# 작업자 안에서 주기를 넘기기 전에 예외가 나면 이만큼 쉬고 다시 시도한다(빈 회전 방지).
WORKER_CRASH_BACKOFF_SEC = 1.0
# 포털 호출량 초과가 이어지면 이 횟수마다 다시 ERROR 로 남긴다(첫 번째는 항상).
QUOTA_EXCEEDED_LOG_EVERY = 100

# 대상 주기에 맞춘 타임아웃(초)을 받아 그 대상 전용 클라이언트를 만든다.
ClientFactory = Callable[[float], GbisClient]
Job = tuple[str, Callable[[], None]]


class WorkerRunner(Protocol):
    def run_all(self, jobs: Sequence[Job], on_poll: Callable[[], object] | None = None) -> None:
        """jobs 를 동시에 돌리고 모두 끝날 때까지 기다린다.

        on_poll 은 기다리는 동안 주기적으로(메인 스레드에서) 부른다(워치독).
        """
        ...


class ThreadRunner:
    """작업자마다 스레드 하나. 메인 스레드는 짧게 나눠 기다려 신호(SIGTERM·Ctrl+C)를 받는다."""

    def __init__(self, join_poll_sec: float = 1.0) -> None:
        self._join_poll_sec = join_poll_sec

    def run_all(self, jobs: Sequence[Job], on_poll: Callable[[], object] | None = None) -> None:
        threads = [threading.Thread(target=fn, name=name, daemon=True) for name, fn in jobs]
        for thread in threads:
            thread.start()
        for thread in threads:
            while thread.is_alive():
                thread.join(self._join_poll_sec)
                if on_poll is not None:
                    try:
                        on_poll()
                    except Exception as exc:  # 워치독 실패가 수집 종료를 막으면 안 된다
                        logger.error("watchdog_failed error=%s", type(exc).__name__)


@dataclass(frozen=True)
class PlannedCall:
    endpoint: GbisEndpoint
    params: dict[str, str]
    label: str  # 로그용(노선 이름 또는 정류장 이름)
    interval_sec: int  # 이 대상의 호출 주기(초). JSONL 의 interval_sec
    key: str = ""  # 대상 키 'location:<노선>' / 'arrival:<정류장>'. 상태 파일 targets 의 키
    save_to_db: bool = True  # DB 적재 대상(라벨 대상 노선과 도착 정류장)


@dataclass(frozen=True)
class PollOutcome:
    planned: PlannedCall
    collected_at: datetime
    result: CallResult | None  # 상한으로 건너뛰면 None
    jsonl_path: Path | None
    skipped_reason: str | None = None


def missing_target_ids(target: CollectTarget) -> list[str]:
    missing: list[str] = []
    seen: set[str] = set()
    for route in (*target.routes, *target.collect_routes):
        if route.route_name in seen:
            continue
        seen.add(route.route_name)
        if not route.route_id:
            missing.append(f"COLLECT_TARGET.routes[{route.route_name}].route_id")
    if not target.board_station_id:
        missing.append(f"COLLECT_TARGET.board_station_id ({target.board_station_name})")
    return missing


def plan_calls(target: CollectTarget, interval_override: int | None = None) -> list[PlannedCall]:
    """호출 대상과 주기(목록 순서).

    interval_override(시운전 --interval-sec)가 있으면 모두 그 간격이다.
    """
    calls = [
        PlannedCall(
            GBIS_BUS_LOCATION,
            {"routeId": str(route.route_id)},
            route.route_name,
            interval_override or route.interval_sec,
            key=f"location:{route.route_name}",
            save_to_db=route.label_target,
        )
        for route in target.collect_routes
        if route.route_id
    ]
    if target.board_station_id:
        calls.append(
            PlannedCall(
                GBIS_BUS_ARRIVAL,
                {"stationId": str(target.board_station_id)},
                target.board_station_name,
                interval_override or target.arrival_interval_sec,
                key=f"arrival:{target.board_station_name}",
                save_to_db=True,
            )
        )
    return calls


def base_interval(calls: list[PlannedCall]) -> int:
    """모든 주기의 최대공약수(참고값. 상태 파일 interval_sec). 대상이 없으면 기본 간격."""
    if not calls:
        return COLLECT_WINDOW.interval_sec
    return math.gcd(*(c.interval_sec for c in calls))


def plan_problems(
    target: CollectTarget = COLLECT_TARGET, limits: CallLimits = CALL_LIMITS
) -> list[str]:
    """정식 수집 설정 점검. 문제가 없으면 빈 목록.

    주기가 허용 범위(10~60초)·86400 약수가 아니거나, 대상 이름이 겹치거나,
    API 별 하루 예상 호출 수가 그 API 의 계획 상한(위치 9,000, 도착 950)을 넘으면 사유를 돌려준다.
    """
    problems: list[str] = []
    calls = plan_calls(target)
    for call in calls:
        try:
            validate_trial_interval(call.interval_sec)
        except ValueError as exc:
            problems.append(
                f"{call.endpoint.api}:{call.label} interval_sec={call.interval_sec} {exc}"
            )
    keys = [c.key for c in calls]
    duplicated = sorted({k for k in keys if keys.count(k) > 1})
    if duplicated:
        problems.append(f"대상 이름이 겹친다: {duplicated}")
    for service, count in planned_daily_calls(target).items():
        planned_max = limits.planned_max_for(service)
        if count > planned_max:
            problems.append(f"{service} 하루 예상 {count}회 > 계획 상한 {planned_max}회")
    return problems


@dataclass
class _WindowLog:
    """창 진입·종료·창 밖 대기 로그를 작업자 수만큼 반복하지 않으려고 둔다."""

    entered_day: date | None = None
    closed_day: date | None = None
    announced_next_start: datetime | None = None
    last_heartbeat: datetime | None = None
    in_window_keys: set[str] = field(default_factory=set)  # 지금 창 안에서 도는 작업자


class Collector:
    def __init__(
        self,
        *,
        data_dir: Path,
        client_factory: ClientFactory,
        clock: Clock,
        redactor: Redactor,
        mode: str,
        target: CollectTarget = COLLECT_TARGET,
        limits: CallLimits = CALL_LIMITS,
        db_sink: RawPollSink | None = None,
        sleep: Callable[[float], object] | None = None,
        max_sleep_chunk_sec: float = 1.0,
        interval_sec: int | None = None,
        runner: WorkerRunner | None = None,
    ) -> None:
        """interval_sec 가 None 이면 대상별 주기(settings)를 쓴다(정식 수집).

        값이 있으면 모든 대상을 그 간격으로 부른다(시운전 --interval-sec 전용).
        client_factory 는 대상마다 한 번 불려 그 대상 전용 클라이언트를 만든다.
        sleep 은 작업자 스레드 안에서 불린다(기본: stop 이벤트 대기라 SIGTERM 에 바로 깬다).
        """
        self.data_dir = data_dir
        self.interval_override = interval_sec
        self.planned_calls = plan_calls(target, interval_sec)
        # 모든 주기의 최대공약수(참고값).
        self.interval_sec = base_interval(self.planned_calls)
        # 오늘 예상 호출 수는 대상별 주기를 쓰는 정식 설정에서만 의미가 있다.
        self.planned_daily = planned_daily_calls(target) if interval_sec is None else None
        self._intervals = {p.key: p.interval_sec for p in self.planned_calls}
        self.clock = clock
        self.redactor = redactor
        self.mode = mode
        self.target = target
        self.db_sink = db_sink
        self._client_factory = client_factory
        self._clients: dict[str, GbisClient] = {}
        self._clients_lock = threading.Lock()
        self._stop = threading.Event()
        self._sleep = sleep or self._stop.wait
        self._max_sleep_chunk_sec = max_sleep_chunk_sec
        self._runner = runner or ThreadRunner()
        # 상태(메모리) 변경은 _status_lock, 상태 파일 쓰기는 _save_lock, JSONL 은 _jsonl_lock.
        # 잠금을 쥔 채 호출·잠자기를 하지 않는다.
        self._status_lock = threading.RLock()
        self._save_lock = threading.Lock()
        self._jsonl_lock = threading.Lock()
        self._db_lock = threading.Lock()
        self._status_version = 0  # 메모리 상태가 저장을 요청한 횟수
        self._saved_version = 0  # 파일에 반영된 마지막 요청 번호
        self._window_log = _WindowLog()
        # 워치독: 대상별 마지막 시도 시각(건너뛴 시도 포함), 정체로 기록한 대상, 감시 시작 시각.
        self._last_attempt: dict[str, datetime] = {}
        self._stalled_keys: set[str] = set()
        self._watch_since: datetime | None = None
        self._watch_until: datetime | None = None  # 시운전이면 until
        self.status = StatusStore(
            status_path(data_dir),
            redactor,
            limits,
            today=to_kst(clock.now()).date(),
            mode=mode,
            db_enabled=db_sink is not None,
        )

    # -- 종료 -------------------------------------------------------------------
    def stop(self) -> None:
        self._stop.set()

    @property
    def is_stopping(self) -> bool:
        return self._stop.is_set()

    def close(self) -> None:
        """대상별 HTTP 클라이언트를 닫는다. 작업자가 모두 끝난 뒤 부른다."""
        with self._clients_lock:
            clients = list(self._clients.values())
            self._clients.clear()
        for client in clients:
            try:
                client.close()
            except Exception as exc:  # 종료 정리 실패가 데이터에 영향을 주지는 않는다. 기록만 한다.
                logger.warning(
                    "client_close_failed error=%s", describe_exception(exc, self.redactor)
                )

    def _client_for(self, planned: PlannedCall) -> GbisClient:
        with self._clients_lock:
            client = self._clients.get(planned.key)
            if client is None:
                client = self._client_factory(call_timeout_sec(planned.interval_sec))
                self._clients[planned.key] = client
            return client

    # -- 1회(once) ----------------------------------------------------------------
    def due_calls(self, tick: datetime | None) -> list[PlannedCall]:
        """tick 이 자기 주기의 경계인 대상. tick 이 None 이면(once) 전부."""
        if tick is None:
            return list(self.planned_calls)
        return [p for p in self.planned_calls if is_on_boundary(tick, p.interval_sec)]

    def poll_cycle(self, tick: datetime | None = None) -> list[PollOutcome]:
        """due_calls(tick) 을 이 스레드에서 차례로 호출한다(once 와 테스트용)."""
        outcomes: list[PollOutcome] = []
        for planned in self.due_calls(tick):
            if self.is_stopping:
                break
            outcomes.append(self.poll_target(planned))
        return outcomes

    # -- 대상 1건 호출 -------------------------------------------------------------
    def poll_target(self, planned: PlannedCall) -> PollOutcome:
        """한 대상을 1회 호출한다: 카운트 저장 → 호출 → JSONL → (DB) → 상태 파일."""
        service = planned.endpoint.service
        collected_at = to_kst(self.clock.now())
        first_cap = False
        calls = limit = 0
        with self._status_lock:
            self._last_attempt[planned.key] = collected_at  # 워치독: 건너뛰어도 시도로 본다
            self._prepare_status(collected_at)
            is_allowed = self.status.can_call(service)
            is_throttle_skip = False
            if is_allowed and not self.status.take_probe(
                service, collected_at, QUOTA_THROTTLE_PROBE_INTERVAL_SEC
            ):
                is_allowed, is_throttle_skip = False, True
                self.status.add_target_throttle_skip(planned.key, planned.interval_sec)
            if is_allowed:
                self.status.begin_call(service, collected_at)
                self.status.begin_target_call(planned.key, planned.interval_sec, collected_at)
            elif not is_throttle_skip:
                first_cap = self.status.mark_capped(service, collected_at)
                self.status.add_target_cap_skip(planned.key, planned.interval_sec)
                calls, limit = self.status.calls(service), self.status.limit_for(service)

        if is_throttle_skip:
            return PollOutcome(planned, collected_at, None, None, skipped_reason="throttled")
        if not is_allowed:
            if first_cap:
                logger.error(
                    "call_cap_reached service=%s calls=%d limit=%d "
                    "action=stop_all_workers_of_this_api_until_tomorrow",
                    service,
                    calls,
                    limit,
                )
                self._persist_status()
            return PollOutcome(planned, collected_at, None, None, skipped_reason="daily_cap")

        # 호출 전에 카운트를 저장한다. 호출 도중 죽어도 하루 호출 수가 적게 남지 않는다.
        self._persist_status()
        try:
            result = self._client_for(planned).call(planned.endpoint, planned.params)
        except Exception as exc:  # 작업자를 멈추지 않는다. 기록하고 다음 주기로 간다.
            message = describe_exception(exc, self.redactor)
            logger.error("poll_crashed api=%s target=%s error=%s", service, planned.label, message)
            self._finish_call(planned, ok=False, error=message, at=collected_at)
            return PollOutcome(planned, collected_at, None, None, skipped_reason="exception")

        if result.is_quota_exceeded:
            self._report_quota_exceeded(planned, result, collected_at)

        try:
            record = build_record(
                collected_at=collected_at,
                result=result,
                mode=self.mode,
                interval_sec=planned.interval_sec,
            )
        except Exception as exc:
            message = describe_exception(exc, self.redactor)
            logger.error(
                "record_build_failed api=%s target=%s error=%s", service, planned.label, message
            )
            self._finish_call(planned, ok=False, error=message, at=collected_at, result=result)
            return PollOutcome(planned, collected_at, result, None, skipped_reason="record_error")

        jsonl_path = self._append_jsonl(record, collected_at)
        if planned.save_to_db:
            self._save_to_db(record, collected_at)
        self._finish_call(planned, ok=result.ok, error=result.error, at=collected_at, result=result)
        self._log_result(planned, result)
        return PollOutcome(planned, collected_at, result, jsonl_path)

    def _prepare_status(self, now: datetime) -> None:
        """_status_lock 안에서 부른다. 날짜가 바뀌었으면 새로 세고, 창·계획을 적는다."""
        self.status.roll_to(now.date())
        self.status.set_in_window(is_in_window(now))
        self.status.set_plan(
            interval_sec=self.interval_sec,
            intervals=dict(self._intervals),
            planned_daily_calls=self.planned_daily,
        )

    def _finish_call(
        self,
        planned: PlannedCall,
        *,
        ok: bool,
        error: str | None,
        at: datetime,
        result: CallResult | None = None,
    ) -> None:
        """호출 결과를 상태에 반영하고 저장한다. result 가 있으면 호출량 초과 감속도 갱신한다."""
        service = planned.endpoint.service
        throttle_change: str | None = None
        with self._status_lock:
            self.status.end_call(service, ok=ok, error=error, at=at)
            self.status.end_target_call(
                planned.key,
                planned.interval_sec,
                ok=ok,
                at=at,
                is_empty=result is not None and result.is_empty,
            )
            if result is not None:
                throttle_change = self.status.record_quota_outcome(
                    service,
                    is_quota_exceeded=result.is_quota_exceeded,
                    ok=result.ok,
                    at=at,
                    throttle_after=QUOTA_THROTTLE_AFTER_CONSECUTIVE,
                    probe_interval_sec=QUOTA_THROTTLE_PROBE_INTERVAL_SEC,
                )
        if throttle_change == "started":
            logger.error(
                "quota_throttle_started service=%s consecutive_quota_exceeded=%d "
                "probe_every_sec=%d action=probe_only_until_normal_response",
                service,
                QUOTA_THROTTLE_AFTER_CONSECUTIVE,
                QUOTA_THROTTLE_PROBE_INTERVAL_SEC,
            )
        elif throttle_change == "ended":
            logger.info(
                "quota_throttle_ended service=%s target=%s action=back_to_normal_intervals",
                service,
                planned.label,
            )
        self._persist_status()

    def _append_jsonl(self, record: dict[str, Any], at: datetime) -> Path | None:
        """한 파일에 줄마다 flush+fsync. 디스크·직렬화 오류는 상태 파일에 남기고 계속한다."""
        target_path = raw_poll_path(self.data_dir, at.date())
        waiting_since = time.monotonic()
        try:
            with self._jsonl_lock:
                self._warn_if_slow_lock("jsonl", waiting_since)
                append_jsonl(target_path, record, self.redactor)
            return target_path
        except Exception as exc:
            message = describe_exception(exc, self.redactor)
            logger.error("jsonl_write_failed path=%s error=%s", target_path, message)
            with self._status_lock:
                self.status.record_jsonl_failure(message, at)
            return None

    def _save_to_db(self, record: dict[str, Any], at: datetime) -> None:
        if self.db_sink is None:
            return
        try:
            with self._db_lock:
                self.db_sink.save(record)
        except Exception as exc:  # DB 실패가 수집을 멈추게 하면 안 된다. JSONL 이 기준이다.
            message = describe_exception(exc, self.redactor)
            logger.warning("db_save_failed api=%s error=%s", record.get("api"), message)
            with self._status_lock:
                self.status.record_db_failure(message, at)

    def _report_quota_exceeded(
        self, planned: PlannedCall, result: CallResult, at: datetime
    ) -> None:
        service = planned.endpoint.service
        with self._status_lock:
            is_first = self.status.record_quota_exceeded(service, at)
            count = int(self.status.api(service)["quota_exceeded"])
        if is_first:
            logger.error(
                "GBIS_DAILY_QUOTA_EXCEEDED service=%s api=%s target=%s at=%s error=%s "
                "action=check_portal_quota_and_other_processes_using_this_key",
                service,
                result.api,
                planned.label,
                at.isoformat(),
                result.error,
            )
        elif count % QUOTA_EXCEEDED_LOG_EVERY == 0:
            logger.error("gbis_daily_quota_exceeded_continues service=%s count=%d", service, count)

    def _persist_status(self) -> None:
        """상태 파일 저장(묶어 쓰기).

        요청 번호를 받고 쓰기 잠금을 얻는다. 기다리는 동안 다른 작업자가 이 요청 이후의 내용까지
        이미 썼으면 쓰지 않는다. 그래서 같은 순간에 여러 작업자가 요청해도 파일 쓰기는 한두 번이다.
        이 함수가 돌아오면 지금까지의 메모리 변경은 파일에 있다(쓰기 실패 시 제외).
        """
        with self._status_lock:
            self._status_version += 1
            wanted = self._status_version
        waiting_since = time.monotonic()
        with self._save_lock:
            self._warn_if_slow_lock("status", waiting_since)
            if self._saved_version >= wanted:
                return
            with self._status_lock:
                version = self._status_version
                snapshot = self.status.snapshot(to_kst(self.clock.now()))
            try:
                self.status.write(snapshot)
            except Exception as exc:  # 상태 파일 실패가 수집을 멈추게 하면 안 된다.
                logger.error("status_write_failed error=%s", describe_exception(exc, self.redactor))
                return
            self._saved_version = version

    def _warn_if_slow_lock(self, name: str, waiting_since: float) -> None:
        waited = time.monotonic() - waiting_since
        if waited >= LOCK_WAIT_WARN_SEC:
            logger.warning(
                "lock_wait_slow lock=%s wait_ms=%d thread=%s",
                name,
                int(waited * 1000),
                threading.current_thread().name,
            )

    def _log_result(self, planned: PlannedCall, result: CallResult) -> None:
        with self._status_lock:
            counters = dict(self.status.api(result.service))
        if result.ok:
            # 성공은 주기마다 반복되므로 DEBUG.
            # 살아 있는지는 heartbeat(INFO)와 status 명령으로 본다.
            logger.debug(
                "poll api=%s target=%s ok=true http=%s code=%s items=%d ms=%d calls=%d",
                result.api,
                planned.label,
                result.http_status,
                result.result_code,
                len(result.items),
                result.elapsed_ms,
                counters["calls"],
            )
        else:
            logger.warning(
                "poll_failed api=%s target=%s http=%s code=%s error=%s ms=%d "
                "consecutive_failures=%d calls=%d",
                result.api,
                planned.label,
                result.http_status,
                result.result_code,
                result.error,
                result.elapsed_ms,
                counters["consecutive_failures"],
                counters["calls"],
            )

    # -- 오래 도는 작업자 ----------------------------------------------------------
    def sleep_until(self, target: datetime) -> None:
        """target 까지 잔다.

        시계를 매번 다시 읽어 절전·시계 변경에도 늦게 깨지 않게 한다.
        """
        while not self.is_stopping:
            remaining = (target - self.clock.now()).total_seconds()
            if remaining <= 0:
                return
            self._sleep(min(remaining, self._max_sleep_chunk_sec))

    def run(self, *, exit_after_window: bool = False) -> None:
        """대상마다 작업자를 띄워 수집 창 안에서 자기 주기 경계마다 호출한다.

        늦어진 주기는 몰아서 호출하지 않고 건너뛴다(대상별로 센다).
        exit_after_window 면 작업자마다 그날 창이 끝나거나 오늘 창이 없을 때 끝나고,
        모두 끝나면 돌아온다. stop()(SIGTERM·Ctrl+C) 이면 모든 작업자가 진행 중인 호출을
        마무리하고 끝난다.
        """
        logger.info(
            "collector_started mode=%s exit_after_window=%s workers=%d targets=%s data_dir=%s",
            self.mode,
            exit_after_window,
            len(self.planned_calls),
            self._describe_targets(),
            self.data_dir,
        )
        self._run_workers(until=None, exit_after_window=exit_after_window)
        self._finish_run("collector_stopped")

    def run_trial(self, until: datetime) -> None:
        """시운전: 수집 창·요일을 무시하고 until 미만의 자기 주기 경계마다 호출한 뒤 돌아온다.

        until 에 닿으면 모든 작업자가 멈춘다. 한도·JSONL·상태 파일은 run 과 같은 경로를 쓴다.
        """
        until = to_kst(until)
        logger.info(
            "trial_started mode=%s until=%s workers=%d targets=%s",
            self.mode,
            until.isoformat(),
            len(self.planned_calls),
            self._describe_targets(),
        )
        self._run_workers(until=until, exit_after_window=False)
        self._finish_run("trial_finished")

    def _describe_targets(self) -> str:
        return ",".join(f"{p.key}={p.interval_sec}s" for p in self.planned_calls)

    def _run_workers(self, *, until: datetime | None, exit_after_window: bool) -> None:
        jobs: list[Job] = [
            (
                f"collector-{planned.key}",
                partial(
                    self._worker_main,
                    planned,
                    until=until,
                    exit_after_window=exit_after_window,
                ),
            )
            for planned in self.planned_calls
        ]
        if not jobs:
            logger.warning("no_targets action=exit")
            return
        with self._status_lock:
            self._watch_until = until
            self._watch_since = None
            self._stalled_keys.clear()
        self._runner.run_all(jobs, on_poll=self.check_stalled)

    # -- 워치독 -------------------------------------------------------------------
    def check_stalled(self) -> list[str]:
        """창 안에서 max(3 × 주기, 60초) 넘게 시도가 없는 대상을 ERROR 로 남긴다. 기록만 한다.

        같은 정체 구간에는 한 번만 남기고, 그 대상이 다시 시도하면 새 구간으로 본다.
        아직 한 번도 시도하지 않은 대상은 감시가 창 안을 처음 본 시각부터 잰다.
        돌려주는 값은 이번에 새로 정체로 기록한 대상 키(테스트용).
        """
        now = to_kst(self.clock.now())
        is_watching = (
            now < self._watch_until if self._watch_until is not None else is_in_window(now)
        )
        newly_stalled: list[tuple[PlannedCall, datetime]] = []
        resumed: list[str] = []
        with self._status_lock:
            if not is_watching:
                self._watch_since = None
                self._stalled_keys.clear()
                return []
            if self._watch_since is None:
                self._watch_since = now
            for planned in self.planned_calls:
                last = self._last_attempt.get(planned.key)
                since = max(last, self._watch_since) if last is not None else self._watch_since
                limit_sec = max(WORKER_STALL_INTERVALS * planned.interval_sec, WORKER_STALL_MIN_SEC)
                if (now - since).total_seconds() > limit_sec:
                    if planned.key not in self._stalled_keys:
                        self._stalled_keys.add(planned.key)
                        newly_stalled.append((planned, since))
                elif planned.key in self._stalled_keys:
                    self._stalled_keys.discard(planned.key)
                    resumed.append(planned.key)
        for planned, since in newly_stalled:
            logger.error(
                "worker_stalled target=%s since=%s interval_sec=%d now=%s action=log_only",
                planned.key,
                since.isoformat(),
                planned.interval_sec,
                now.isoformat(),
            )
        for key in resumed:
            logger.info("worker_resumed target=%s now=%s", key, now.isoformat())
        return [planned.key for planned, _ in newly_stalled]

    def _finish_run(self, event: str) -> None:
        self._log_summary(event)
        self._persist_status()

    def _worker_main(
        self, planned: PlannedCall, *, until: datetime | None, exit_after_window: bool
    ) -> None:
        """작업자 스레드의 입구. 루프 밖으로 예외가 새어도 다시 들어가 수집을 이어 간다."""
        while not self.is_stopping:
            try:
                self._worker_loop(planned, until=until, exit_after_window=exit_after_window)
                return
            except Exception as exc:
                logger.error(
                    "worker_restarted target=%s error=%s",
                    planned.key,
                    describe_exception(exc, self.redactor),
                )
                self._sleep(WORKER_CRASH_BACKOFF_SEC)

    def _worker_loop(
        self, planned: PlannedCall, *, until: datetime | None, exit_after_window: bool
    ) -> None:
        """한 대상의 루프. until 이 있으면 시운전(창 무시), 없으면 수집 창 안에서만 호출한다."""
        is_trial = until is not None
        last_tick: datetime | None = None
        while not self.is_stopping:
            has_moved_on = False
            try:
                now = to_kst(self.clock.now())
                # 창 밖 대기에서 05:30:00.003 처럼 경계를 살짝 넘겨 깨어나도 05:30 주기로 본다.
                tick = next_tick(now, last_tick, planned.interval_sec)
                if until is not None and tick >= until:
                    return
                if not is_trial and not is_in_window(tick):
                    # now 가 아니라 다음 주기 시각으로 판단한다.
                    # 예: 10:14:50 호출 직후 → 다음 주기 10:15:00 은 창 밖 → 종료.
                    if exit_after_window and is_today_window_over(tick):
                        self._note_outside_window(planned.key, now, None)
                        logger.info(
                            "worker_exit target=%s reason=window_over now=%s",
                            planned.key,
                            now.isoformat(),
                        )
                        return
                    next_start = next_window_start(tick)
                    self._note_outside_window(planned.key, now, next_start)
                    self.sleep_until(next_start)
                    last_tick = None
                    has_moved_on = True
                    continue
                self.sleep_until(tick)
                if self.is_stopping:
                    return
                if last_tick is not None:
                    self._record_skipped(planned, last_tick, tick)
                if not is_trial:
                    self._note_window_entered(planned.key, tick)
                last_tick = tick
                has_moved_on = True
                # 한 주기의 실패가 작업자를 죽이면 이후 데이터를 잃는다. 기록하고 계속한다.
                self.poll_target(planned)
                self._maybe_heartbeat()
            except Exception as exc:
                logger.error(
                    "cycle_crashed target=%s tick=%s error=%s",
                    planned.key,
                    last_tick.isoformat() if last_tick else None,
                    describe_exception(exc, self.redactor),
                )
                if not has_moved_on:
                    self._sleep(WORKER_CRASH_BACKOFF_SEC)

    def _record_skipped(self, planned: PlannedCall, last_tick: datetime, tick: datetime) -> None:
        interval = timedelta(seconds=planned.interval_sec)
        skipped = int((tick - last_tick) / interval) - 1
        if skipped <= 0:
            return
        with self._status_lock:
            self.status.add_target_skipped(planned.key, planned.interval_sec, skipped)
        logger.warning(
            "cycles_skipped target=%s count=%d from=%s to=%s reason=late_cycle",
            planned.key,
            skipped,
            (last_tick + interval).isoformat(),
            (tick - interval).isoformat(),
        )

    def _note_window_entered(self, key: str, tick: datetime) -> None:
        log = self._window_log
        with self._status_lock:
            log.in_window_keys.add(key)
            if log.entered_day == tick.date():
                return
            log.entered_day = tick.date()
        logger.info("window_entered at=%s", tick.isoformat())

    def _note_outside_window(self, key: str, now: datetime, next_start: datetime | None) -> None:
        """작업자가 창 밖에 있다.

        창 종료 요약은 오늘 창에서 마지막 작업자가 나갈 때, 대기 로그는 대기 끝 시각마다 한 번만
        남긴다(작업자 수만큼 반복하지 않는다).
        """
        day = now.date()
        log = self._window_log
        with self._status_lock:
            log.in_window_keys.discard(key)
            is_last_out = not log.in_window_keys
            is_closing = is_last_out and log.entered_day == day and log.closed_day != day
            if is_closing:
                log.closed_day = day
            is_new_wait = next_start is not None and next_start != log.announced_next_start
            if is_new_wait:
                log.announced_next_start = next_start
            has_rolled = self.status.roll_to(day)
            was_in_window = False
            if is_last_out:
                was_in_window = self.status.data.get("in_window") is not False
                self.status.set_in_window(False)
        if is_closing:
            self._log_summary("window_closed")
        if is_new_wait and next_start is not None:
            logger.info("outside_window sleep_until=%s", next_start.isoformat())
        if is_closing or is_new_wait or has_rolled or was_in_window:
            self._persist_status()

    def _maybe_heartbeat(self) -> None:
        now = to_kst(self.clock.now())
        log = self._window_log
        with self._status_lock:
            if log.last_heartbeat is None:
                log.last_heartbeat = now
                return
            if (now - log.last_heartbeat).total_seconds() < HEARTBEAT_EVERY_SEC:
                return
            log.last_heartbeat = now
        self._log_summary("heartbeat")

    def _log_summary(self, event: str) -> None:
        with self._status_lock:
            data = self.status.data
            apis = " ".join(
                f"{service}={c.get('calls', 0)}/{c.get('success', 0)}/{c.get('failure', 0)}"
                for service, c in sorted(data.get("apis", {}).items())
            )
            targets = " ".join(
                f"{key.split(':', 1)[-1]}="
                f"{t.get('calls', 0)}/{t.get('failure', 0)}/{t.get('skipped_cycles', 0)}"
                for key, t in data.get("targets", {}).items()
            )
            day, last_success = data.get("date"), data.get("last_success_at")
        logger.info(
            "%s date=%s calls/success/failure: %s targets calls/failure/skipped: %s "
            "last_success_at=%s",
            event,
            day,
            apis or "-",
            targets or "-",
            last_success,
        )
