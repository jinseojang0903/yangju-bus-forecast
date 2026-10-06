"""수집 주기와 오래 도는 루프.

한 주기 = 위치 v2 × 노선 수 + 도착 v2 × 1.
호출마다 JSONL 한 줄 → (DB) → 상태 파일 순으로 남긴다.
수집에 실패한 시간의 데이터는 다시 얻을 수 없으므로,
어떤 예외도 루프를 멈추지 않게 하고 기록한다.
"""

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from app.collector.db import RawPollSink
from app.collector.gbis import CallResult, GbisClient
from app.collector.redact import Redactor, describe_exception
from app.collector.schedule import (
    Clock,
    is_in_window,
    is_today_window_over,
    next_tick,
    next_window_start,
    to_kst,
)
from app.collector.status import StatusStore, status_path
from app.collector.storage import append_jsonl, build_record, raw_poll_path
from app.core.settings import (
    CALL_LIMITS,
    COLLECT_TARGET,
    COLLECT_WINDOW,
    GBIS_BUS_ARRIVAL,
    GBIS_BUS_LOCATION,
    CallLimits,
    CollectTarget,
    GbisEndpoint,
)

logger = logging.getLogger(__name__)

MODE_RUN = "run"
MODE_ONCE = "once"
# 시운전. 평가·사례에서 이 mode 의 기록은 뺀다.
MODE_TRIAL = "trial"
# 수집 창 안에서 이만큼의 주기마다 INFO 로 누적 호출 수를 남긴다(약 30분).
HEARTBEAT_EVERY_CYCLES = 30


@dataclass(frozen=True)
class PlannedCall:
    endpoint: GbisEndpoint
    params: dict[str, str]
    label: str  # 로그용(노선 번호 또는 정류장 이름)


@dataclass(frozen=True)
class PollOutcome:
    planned: PlannedCall
    collected_at: datetime
    result: CallResult | None  # 상한으로 건너뛰면 None
    jsonl_path: Path | None
    skipped_reason: str | None = None


def missing_target_ids(target: CollectTarget) -> list[str]:
    missing = [
        f"COLLECT_TARGET.routes[{r.route_name}].route_id" for r in target.routes if not r.route_id
    ]
    if not target.board_station_id:
        missing.append(f"COLLECT_TARGET.board_station_id ({target.board_station_name})")
    return missing


def plan_calls(target: CollectTarget) -> list[PlannedCall]:
    calls = [
        PlannedCall(GBIS_BUS_LOCATION, {"routeId": str(route.route_id)}, route.route_name)
        for route in target.routes
        if route.route_id
    ]
    if target.board_station_id:
        calls.append(
            PlannedCall(
                GBIS_BUS_ARRIVAL,
                {"stationId": str(target.board_station_id)},
                target.board_station_name,
            )
        )
    return calls


class Collector:
    def __init__(
        self,
        *,
        data_dir: Path,
        client: GbisClient,
        clock: Clock,
        redactor: Redactor,
        mode: str,
        target: CollectTarget = COLLECT_TARGET,
        limits: CallLimits = CALL_LIMITS,
        db_sink: RawPollSink | None = None,
        sleep: Callable[[float], object] | None = None,
        max_sleep_chunk_sec: float = 1.0,
    ) -> None:
        self.data_dir = data_dir
        self.client = client
        self.clock = clock
        self.redactor = redactor
        self.mode = mode
        self.target = target
        self.db_sink = db_sink
        self._stop = threading.Event()
        # 기본 잠자기는 stop 이벤트 대기라 SIGTERM·Ctrl+C 에 바로 깬다.
        self._sleep = sleep or self._stop.wait
        self._max_sleep_chunk_sec = max_sleep_chunk_sec
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

    # -- 한 주기 -----------------------------------------------------------------
    def poll_cycle(self) -> list[PollOutcome]:
        now = to_kst(self.clock.now())
        self.status.roll_to(now.date())
        self.status.set_in_window(is_in_window(now))
        outcomes: list[PollOutcome] = []
        for planned in plan_calls(self.target):
            if self.is_stopping:
                break
            outcomes.append(self._poll_one(planned))
        return outcomes

    def _poll_one(self, planned: PlannedCall) -> PollOutcome:
        service = planned.endpoint.service
        collected_at = to_kst(self.clock.now())

        if not self.status.can_call(service):
            if self.status.mark_capped(service, collected_at):
                logger.error(
                    "call_cap_reached service=%s calls=%d action=stop_calls_until_tomorrow",
                    service,
                    self.status.calls(service),
                )
                self._save_status(collected_at)
            return PollOutcome(planned, collected_at, None, None, skipped_reason="daily_cap")

        # 호출 전에 카운트를 저장한다. 호출 도중 죽어도 하루 호출 수가 적게 남지 않는다.
        self.status.begin_call(service, collected_at)
        self._save_status(collected_at)
        try:
            result = self.client.call(planned.endpoint, planned.params)
        except Exception as exc:  # 수집 루프를 멈추지 않는다. 기록하고 다음 호출로 간다.
            message = describe_exception(exc, self.redactor)
            logger.error("poll_crashed api=%s target=%s error=%s", service, planned.label, message)
            self.status.end_call(service, ok=False, error=message, at=collected_at)
            self._save_status(collected_at)
            return PollOutcome(planned, collected_at, None, None, skipped_reason="exception")

        try:
            record = build_record(collected_at=collected_at, result=result, mode=self.mode)
        except Exception as exc:
            message = describe_exception(exc, self.redactor)
            logger.error(
                "record_build_failed api=%s target=%s error=%s", service, planned.label, message
            )
            self.status.end_call(service, ok=False, error=message, at=collected_at)
            self._save_status(collected_at)
            return PollOutcome(planned, collected_at, result, None, skipped_reason="record_error")

        target_path = raw_poll_path(self.data_dir, collected_at.date())
        jsonl_path: Path | None = target_path
        # 디스크 오류·직렬화 오류 모두 상태 파일에 남기고 계속한다.
        try:
            append_jsonl(target_path, record, self.redactor)
        except Exception as exc:
            message = describe_exception(exc, self.redactor)
            logger.error("jsonl_write_failed path=%s error=%s", target_path, message)
            self.status.record_jsonl_failure(message, collected_at)
            jsonl_path = None

        self._save_to_db(record, collected_at)
        self.status.end_call(service, ok=result.ok, error=result.error, at=collected_at)
        self._save_status(collected_at)
        self._log_result(planned, result)
        return PollOutcome(planned, collected_at, result, jsonl_path)

    def _save_to_db(self, record: dict, at: datetime) -> None:
        if self.db_sink is None:
            return
        try:
            self.db_sink.save(record)
        except Exception as exc:  # DB 실패가 수집을 멈추게 하면 안 된다. JSONL 이 기준이다.
            message = describe_exception(exc, self.redactor)
            logger.warning("db_save_failed api=%s error=%s", record.get("api"), message)
            self.status.record_db_failure(message, at)

    def _save_status(self, at: datetime) -> None:
        try:
            self.status.save(at)
        except OSError as exc:
            logger.error("status_write_failed error=%s", describe_exception(exc, self.redactor))

    def _log_result(self, planned: PlannedCall, result: CallResult) -> None:
        counters = self.status.api(result.service)
        if result.ok:
            # 성공은 분마다 반복되므로 DEBUG.
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

    # -- 오래 도는 루프 -----------------------------------------------------------
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
        """분 경계마다 수집 창 안에서만 호출한다.

        늦어진 주기는 몰아서 호출하지 않고 건너뛴다.
        exit_after_window 면 그날 창이 끝나거나 오늘 창이 없을 때 돌아온다.
        """
        interval = timedelta(seconds=COLLECT_WINDOW.interval_sec)
        last_tick: datetime | None = None
        was_in_window: bool | None = None
        cycles = 0
        logger.info(
            "collector_started mode=%s exit_after_window=%s data_dir=%s",
            self.mode,
            exit_after_window,
            self.data_dir,
        )
        while not self.is_stopping:
            now = to_kst(self.clock.now())
            # 창 밖 대기에서 05:00:00.003 처럼 경계를 살짝 넘겨 깨어나도 05:00 주기로 본다.
            tick = next_tick(now, last_tick)

            if not is_in_window(tick):
                if was_in_window:
                    self._log_summary("window_closed")
                if was_in_window is not False:
                    self.status.roll_to(now.date())
                    self.status.set_in_window(False)
                    self._save_status(now)
                    was_in_window = False
                # now 가 아니라 다음 주기 시각으로 판단한다.
                # 예: 09:59 호출 직후 → 다음 주기 10:00 은 창 밖 → 종료.
                if exit_after_window and is_today_window_over(tick):
                    logger.info("collector_exit reason=window_over now=%s", now.isoformat())
                    return
                next_start = next_window_start(tick)
                logger.info("outside_window sleep_until=%s", next_start.isoformat())
                self.sleep_until(next_start)
                last_tick = None
                continue

            self.sleep_until(tick)
            if self.is_stopping:
                break
            if last_tick is not None and tick - last_tick > interval:
                skipped = int((tick - last_tick) / interval) - 1
                logger.warning(
                    "cycles_skipped count=%d from=%s to=%s reason=late_cycle",
                    skipped,
                    (last_tick + interval).isoformat(),
                    (tick - interval).isoformat(),
                )
            if was_in_window is not True:
                logger.info("window_entered at=%s", tick.isoformat())
                was_in_window = True
                cycles = 0
            # 한 주기의 실패가 프로세스를 죽이면 이후 데이터를 잃는다. 기록하고 계속한다.
            try:
                self.poll_cycle()
            except Exception as exc:
                logger.error(
                    "cycle_crashed tick=%s error=%s",
                    tick.isoformat(),
                    describe_exception(exc, self.redactor),
                )
            last_tick = tick
            cycles += 1
            if cycles % HEARTBEAT_EVERY_CYCLES == 0:
                self._log_summary("heartbeat")
        self._log_summary("collector_stopped")

    def run_trial(self, until: datetime) -> None:
        """시운전: 수집 창·요일을 무시하고 until 미만의 분 경계마다 호출한 뒤 돌아온다.

        주기 정렬(next_tick)·한도·JSONL·상태 파일은 run 과 같은 경로를 쓴다.
        """
        interval = timedelta(seconds=COLLECT_WINDOW.interval_sec)
        until = to_kst(until)
        last_tick: datetime | None = None
        logger.info("trial_started mode=%s until=%s", self.mode, until.isoformat())
        while not self.is_stopping:
            tick = next_tick(to_kst(self.clock.now()), last_tick)
            if tick >= until:
                break
            self.sleep_until(tick)
            if self.is_stopping:
                break
            if last_tick is not None and tick - last_tick > interval:
                logger.warning(
                    "cycles_skipped count=%d from=%s reason=late_cycle",
                    int((tick - last_tick) / interval) - 1,
                    (last_tick + interval).isoformat(),
                )
            try:
                self.poll_cycle()
            except Exception as exc:
                logger.error(
                    "cycle_crashed tick=%s error=%s",
                    tick.isoformat(),
                    describe_exception(exc, self.redactor),
                )
            last_tick = tick
        self._log_summary("trial_finished")

    def _log_summary(self, event: str) -> None:
        apis = " ".join(
            f"{service}={c.get('calls', 0)}/{c.get('success', 0)}/{c.get('failure', 0)}"
            for service, c in sorted(self.status.data.get("apis", {}).items())
        )
        logger.info(
            "%s date=%s calls/success/failure: %s last_success_at=%s",
            event,
            self.status.data.get("date"),
            apis or "-",
            self.status.data.get("last_success_at"),
        )
