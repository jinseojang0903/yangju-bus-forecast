"""수집기 명령. 실행: cd backend && uv run python -m app.collector <명령>

    discover      노선·정류장 ID 후보와 근거를 화면에 출력(파일에 쓰지 않음)
    once          지금 대상 전체를 1회 수집해 JSONL 에 저장하고 요약 출력
    run           평일 05:00~10:00(KST) 분 경계마다 수집
                  --exit-after-window: 그날 창이 끝나면 종료
    status        오늘 상태를 한 줄 JSON 으로 출력
    save-fixture  실제 응답(위치·도착) 1건씩을 tests/fixtures/gbis/ 에 저장
                  (서비스 키 제거)

종료 코드: 0 정상, 1 호출 실패 있음, 2 설정 누락(서비스 키·routeId·stationId),
3 이미 실행 중.
"""

import argparse
import io
import json
import logging
import signal
import sys
from collections.abc import Sequence
from types import FrameType

from app.collector.collector import (
    MODE_ONCE,
    MODE_RUN,
    Collector,
    missing_target_ids,
    plan_calls,
)
from app.collector.db import build_raw_poll_sink
from app.collector.discover import discover
from app.collector.fixtures import save_fixture
from app.collector.gbis import GbisClient
from app.collector.lock import AlreadyRunningError, collector_lock, is_collector_running
from app.collector.logging_setup import quiet_http_loggers, setup_logging
from app.collector.redact import Redactor, describe_exception
from app.collector.schedule import SystemClock
from app.collector.status import StatusStore, build_status_report, status_path
from app.collector.summary import summarize
from app.core.settings import (
    CALL_LIMITS,
    COLLECT_TARGET,
    ENV_FILE,
    GBIS_BUS_ARRIVAL,
    GBIS_BUS_LOCATION,
    GBIS_FIXTURE_DIR,
    Settings,
    get_settings,
)

logger = logging.getLogger("app.collector")

EXIT_OK = 0
EXIT_CALL_FAILED = 1
EXIT_CONFIG_MISSING = 2
EXIT_ALREADY_RUNNING = 3


def _configure_stdout() -> None:
    # systemd 에서는 stdout 이 파이프라 줄 단위로 내보낸다.
    # 콘솔 인코딩이 못 쓰는 글자는 바꿔 쓴다.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(line_buffering=True, errors="replace")


def _require_service_key(settings: Settings) -> bool:
    if settings.has_service_key:
        return True
    logger.error("config_missing name=GBIS_SERVICE_KEY env_file=%s", ENV_FILE)
    return False


def _report_missing_ids() -> bool:
    missing = missing_target_ids(COLLECT_TARGET)
    if not missing:
        return False
    logger.error("config_missing ids=%s", ", ".join(missing))
    print("routeId·stationId 가 설정에 없어 호출하지 않는다:")
    for name in missing:
        print(f"  - {name}")
    print("먼저 `uv run python -m app.collector discover` 를 실행하고,")
    print("그 결과로 backend/app/core/settings.py 의 COLLECT_TARGET 을 채운다.")
    return True


def _new_client(settings: Settings, redactor: Redactor) -> GbisClient:
    return GbisClient(settings.service_key, redactor)


# ---------------------------------------------------------------------------
def cmd_discover(settings: Settings, redactor: Redactor) -> int:
    if not _require_service_key(settings):
        return EXIT_CONFIG_MISSING
    client = _new_client(settings, redactor)
    try:
        return discover(client, print)
    finally:
        client.close()


def cmd_once(settings: Settings, redactor: Redactor) -> int:
    if _report_missing_ids():
        return EXIT_CONFIG_MISSING
    if not _require_service_key(settings):
        return EXIT_CONFIG_MISSING
    data_dir = settings.data_dir
    with collector_lock(data_dir):
        client = _new_client(settings, redactor)
        db_sink = build_raw_poll_sink(settings)
        try:
            collector = Collector(
                data_dir=data_dir,
                client=client,
                clock=SystemClock(),
                redactor=redactor,
                mode=MODE_ONCE,
                db_sink=db_sink,
            )
            outcomes = collector.poll_cycle()
        finally:
            client.close()
            if db_sink is not None:
                db_sink.close()

    has_failure = False
    for outcome in outcomes:
        if outcome.result is None:
            has_failure = True
            print(
                f"[{outcome.planned.endpoint.api}] target={outcome.planned.label} "
                f"건너뜀: {outcome.skipped_reason}"
            )
            continue
        has_failure = has_failure or not outcome.result.ok
        for line in summarize(outcome.result, outcome.planned.label, redactor):
            print(line)
        print(f"  saved: {outcome.jsonl_path if outcome.jsonl_path else '(JSONL 쓰기 실패)'}")
    print(f"status: {status_path(data_dir)}")
    return EXIT_CALL_FAILED if has_failure else EXIT_OK


def cmd_run(settings: Settings, redactor: Redactor, *, exit_after_window: bool) -> int:
    if _report_missing_ids():
        return EXIT_CONFIG_MISSING
    if not _require_service_key(settings):
        return EXIT_CONFIG_MISSING
    data_dir = settings.data_dir
    with collector_lock(data_dir):
        client = _new_client(settings, redactor)
        db_sink = build_raw_poll_sink(settings)
        try:
            collector = Collector(
                data_dir=data_dir,
                client=client,
                clock=SystemClock(),
                redactor=redactor,
                mode=MODE_RUN,
                db_sink=db_sink,
            )

            def _stop(signum: int, _frame: FrameType | None) -> None:
                collector.stop()

            signal.signal(signal.SIGINT, _stop)
            signal.signal(signal.SIGTERM, _stop)
            if hasattr(signal, "SIGBREAK"):  # 윈도우 콘솔 Ctrl+Break
                signal.signal(signal.SIGBREAK, _stop)
            collector.run(exit_after_window=exit_after_window)
        finally:
            client.close()
            if db_sink is not None:
                db_sink.close()
    return EXIT_OK


def cmd_status(settings: Settings, redactor: Redactor) -> int:
    """한 줄 JSON 을 출력한다.

    실패해도 가려진 오류 메시지만 담은 한 줄 JSON 을 출력하고 1 을 돌려준다.
    """
    data_dir = settings.data_dir
    try:
        report = build_status_report(
            data_dir, SystemClock().now(), is_running=is_collector_running(data_dir)
        )
        exit_code = EXIT_OK
    except Exception as exc:  # 진입점 경계. 스택·원문 메시지는 내보내지 않는다.
        report = {"status_error": describe_exception(exc, redactor)}
        exit_code = EXIT_CALL_FAILED
    print(redactor.redact(json.dumps(report, ensure_ascii=False, separators=(",", ":"))))
    return exit_code


def cmd_save_fixture(settings: Settings, redactor: Redactor) -> int:
    if _report_missing_ids():
        return EXIT_CONFIG_MISSING
    if not _require_service_key(settings):
        return EXIT_CONFIG_MISSING
    planned = plan_calls(COLLECT_TARGET)
    location = next(p for p in planned if p.endpoint == GBIS_BUS_LOCATION)
    arrival = next(p for p in planned if p.endpoint == GBIS_BUS_ARRIVAL)

    data_dir = settings.data_dir
    clock = SystemClock()
    exit_code = EXIT_OK
    with collector_lock(data_dir):
        status = StatusStore(
            status_path(data_dir),
            redactor,
            CALL_LIMITS,
            today=clock.now().date(),
            mode="save-fixture",
        )
        client = _new_client(settings, redactor)
        try:
            for call in (location, arrival):
                service = call.endpoint.service
                at = clock.now()
                if not status.can_call(service):
                    print(f"[{call.endpoint.api}] 오늘 호출 상한에 닿아 건너뜀")
                    exit_code = EXIT_CALL_FAILED
                    continue
                status.begin_call(service, at)
                status.save(at)  # 호출 전에 카운트를 남긴다
                result = client.call(call.endpoint, call.params)
                status.end_call(service, ok=result.ok, error=result.error, at=at)
                status.save(at)
                for line in summarize(result, call.label, redactor):
                    print(line)
                try:
                    path = save_fixture(result, GBIS_FIXTURE_DIR, redactor, at)
                except ValueError as exc:
                    print(f"  픽스처 저장 안 함: {describe_exception(exc, redactor)}")
                    exit_code = EXIT_CALL_FAILED
                    continue
                print(f"  fixture: {path}")
                if not result.ok:
                    exit_code = EXIT_CALL_FAILED
        finally:
            client.close()
    return exit_code


# ---------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.collector", description="GBIS 실시간 수집기"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("discover", help="노선·정류장 ID 찾기(화면 출력만)")
    sub.add_parser("once", help="대상 전체 1회 수집")
    run = sub.add_parser("run", help="수집 창 안에서 1분마다 수집")
    run.add_argument("--exit-after-window", action="store_true", help="그날 수집 창이 끝나면 종료")
    sub.add_parser("status", help="오늘 상태를 한 줄 JSON 으로 출력")
    sub.add_parser("save-fixture", help="실제 응답을 테스트 픽스처로 저장")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_stdout()
    settings = get_settings()
    redactor = Redactor(settings.secret_values())

    if args.command == "status":
        # 출력이 한 줄 JSON 이어야 하므로 로그 핸들러를 달지 않는다.
        quiet_http_loggers()
        return cmd_status(settings, redactor)

    setup_logging(settings.log_dir, redactor)
    try:
        if args.command == "discover":
            return cmd_discover(settings, redactor)
        if args.command == "once":
            return cmd_once(settings, redactor)
        if args.command == "run":
            return cmd_run(settings, redactor, exit_after_window=args.exit_after_window)
        if args.command == "save-fixture":
            return cmd_save_fixture(settings, redactor)
    except AlreadyRunningError as exc:
        logger.error("already_running detail=%s", exc)
        return EXIT_ALREADY_RUNNING
    except Exception:
        # 진입점 경계. 스택은 로그 필터가 비밀값을 가린 뒤 남긴다.
        # 종료 후에는 systemd 가 다시 띄운다.
        logger.exception("collector_crashed command=%s", args.command)
        return EXIT_CALL_FAILED
    raise AssertionError(f"알 수 없는 명령: {args.command}")
