"""정식 수집(run): 대상별 독립 작업자, 수집 창, 늦은 주기 건너뛰기.

작업자 스레드는 FakeClock 이 가상 시간으로 한 번에 하나씩 돌린다(helpers.FakeClock).
같은 시각에 깨어나는 작업자는 대상 목록 순서(G1300, 1306, …, 도착)로 돈다.
"""

import json
import logging
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.collector import collector as collector_module
from app.collector.collector import Collector
from app.collector.storage import raw_poll_path
from tests.collector.helpers import (
    G1300_ID,
    MIXED_TARGET,
    MULTI_TARGET,
    NIGHT_ID,
    R1100_ID,
    R1306_ID,
    FakeClock,
    OvershootClock,
    RecordingHandler,
    kst,
    load_fixture,
    make_collector,
)


def _jsonl(data_dir: Path, day: str) -> list[dict]:
    path = data_dir / day / "raw_poll.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _status(data_dir: Path) -> dict:
    return json.loads((data_dir / "status.json").read_text(encoding="utf-8"))


def _target_of(record: dict) -> str:
    return record["params"].get("routeId", "arrival")


def _times_by_target(records: list[dict]) -> dict[str, list[str]]:
    """대상별 collected_at 의 HH:MM:SS 목록(기록 순서)."""
    times: dict[str, list[str]] = defaultdict(list)
    for record in records:
        times[_target_of(record)].append(record["collected_at"][11:19])
    return dict(times)


def _minutes(data_dir: Path, day: str) -> list[str]:
    return [r["collected_at"][11:16] for r in _jsonl(data_dir, day)]


def _stop_at(collector_ref: list[Collector], clock: FakeClock, stop_at):
    """작업자 sleep 이 stop_at 이후에 깨어나면 수집기를 멈춘다(SIGTERM 흉내)."""

    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now() >= stop_at:
            collector_ref[0].stop()

    return sleep


def _run_until(
    data_dir: Path,
    clock: FakeClock,
    handler,
    stop_at,
    *,
    exit_after_window: bool = False,
    **kwargs,
) -> Collector:
    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, handler, sleep=_stop_at(ref, clock, stop_at), **kwargs
    )
    ref.append(collector)
    collector.run(exit_after_window=exit_after_window)
    return collector


# ---------------------------------------------------------------------------
# 창과 종료
# ---------------------------------------------------------------------------
def test_exit_after_window_stops_at_1015(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 10, 12, 30))
    collector = make_collector(data_dir, clock, handler)
    collector.run(exit_after_window=True)

    records = _jsonl(data_dir, "2026-10-07")
    assert [r["collected_at"][11:19] for r in records] == ["10:13:00"] * 3 + ["10:14:00"] * 3
    assert {r["mode"] for r in records} == {"run"}
    assert clock.now() < kst(2026, 10, 7, 10, 16)
    assert clock.alive_jobs == 0
    assert _status(data_dir)["in_window"] is False


@pytest.mark.parametrize(
    "start",
    [kst(2026, 10, 10, 6, 0), kst(2026, 10, 7, 10, 30)],
    ids=["saturday", "after-window"],
)
def test_exit_after_window_outside_window_exits_without_calls(data_dir: Path, start) -> None:
    handler = RecordingHandler()
    clock = FakeClock(start)
    make_collector(data_dir, clock, handler).run(exit_after_window=True)
    assert handler.requests == []
    assert clock.now() == start
    assert clock.alive_jobs == 0


def test_run_waits_for_window_and_aligns_to_minute(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 5, 28, 17))
    _run_until(data_dir, clock, handler, kst(2026, 10, 7, 5, 32))

    records = _jsonl(data_dir, "2026-10-07")
    assert [r["collected_at"][11:23] for r in records] == (
        ["05:30:00.000"] * 3 + ["05:31:00.000"] * 3
    )


def test_wake_late_from_window_wait_still_polls_0530_and_every_minute(data_dir: Path) -> None:
    # 04:55 시작, sleep 이 매번 3ms 늦게 깨어나도 05:30 주기를 건너뛰지 않고 빠짐없이 호출한다.
    handler = RecordingHandler()
    clock = OvershootClock(kst(2026, 10, 7, 4, 55))
    _run_until(data_dir, clock, handler, kst(2026, 10, 7, 5, 35, 30), exit_after_window=True)

    records = _jsonl(data_dir, "2026-10-07")
    assert records[0]["collected_at"].startswith("2026-10-07T05:30:00")
    expected = [f"05:3{m}" for m in range(6) for _ in range(3)]
    assert _minutes(data_dir, "2026-10-07") == expected
    assert handler.count("getBusLocationListv2") == 12
    assert handler.count("getBusArrivalListv2") == 6


def test_wake_late_multi_intervals_first_calls_at_0530(data_dir: Path) -> None:
    # 정식 모양(10/30/40초, 도착 30초)으로 04:55 시작 + 늦게 깨어남: 05:30:00 에 넷 다 호출.
    handler = RecordingHandler()
    clock = OvershootClock(kst(2026, 10, 7, 4, 55))
    _run_until(data_dir, clock, handler, kst(2026, 10, 7, 5, 30, 5), target=MULTI_TARGET)

    records = _jsonl(data_dir, "2026-10-07")
    assert [r["collected_at"][11:19] for r in records] == ["05:30:00"] * 4
    assert [_target_of(r) for r in records] == [G1300_ID, R1306_ID, R1100_ID, "arrival"]
    assert [r["interval_sec"] for r in records] == [10, 30, 40, 30]


def test_process_running_overnight_polls_0530_next_day(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = OvershootClock(kst(2026, 10, 7, 10, 15, 30))
    _run_until(data_dir, clock, handler, kst(2026, 10, 8, 5, 31, 30))

    assert _minutes(data_dir, "2026-10-08") == ["05:30"] * 3 + ["05:31"] * 3
    assert not (data_dir / "2026-10-07").exists()


# ---------------------------------------------------------------------------
# 대상별 주기(10/30/40초)
# ---------------------------------------------------------------------------
def test_multi_intervals_each_target_calls_on_its_own_boundaries(data_dir: Path) -> None:
    # 05:30:00 이상 05:32:00 미만. 수집 안 하는 노선(G1300N)은 부르지 않는다.
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 5, 30))
    timeouts: list[float] = []
    collector = _run_until(
        data_dir, clock, handler, kst(2026, 10, 7, 5, 32), target=MULTI_TARGET, timeouts=timeouts
    )

    times = _times_by_target(_jsonl(data_dir, "2026-10-07"))
    assert times[G1300_ID] == [f"05:3{m}:{s}0" for m in (0, 1) for s in range(6)]
    assert times[R1306_ID] == ["05:30:00", "05:30:30", "05:31:00", "05:31:30"]
    assert times[R1100_ID] == ["05:30:00", "05:30:40", "05:31:20"]
    assert times["arrival"] == ["05:30:00", "05:30:30", "05:31:00", "05:31:30"]
    assert handler.count_route(NIGHT_ID) == 0
    # 같은 시각이면 목록 순서로 호출한다.
    first_four = [_target_of(r) for r in _jsonl(data_dir, "2026-10-07")[:4]]
    assert first_four == [G1300_ID, R1306_ID, R1100_ID, "arrival"]
    # 대상마다 클라이언트를 따로 만들고, 타임아웃은 min(10초, 주기 − 2초).
    assert sorted(timeouts) == [8.0, 10.0, 10.0, 10.0]
    assert clock.alive_jobs == 0

    status = _status(data_dir)
    targets = status["targets"]
    assert targets["location:G1300"]["interval_sec"] == 10
    assert targets["location:G1300"]["calls"] == 12
    assert targets["location:1306"]["calls"] == 4
    assert targets["location:1100"]["calls"] == 3
    assert targets["arrival:덕현초교"]["success"] == 4
    assert targets["location:G1300"]["last_success_at"] == "2026-10-07T05:31:50+09:00"
    assert status["intervals"] == {
        "location:G1300": 10,
        "location:1306": 30,
        "location:1100": 40,
        "arrival:덕현초교": 30,
    }
    assert status["apis"]["buslocationservice"]["calls"] == 19
    assert collector.interval_sec == 10


def test_mixed_intervals_last_call_1014_30(data_dir: Path) -> None:
    # 10:14:30 이 마지막 G1300 호출, 10:15:00 은 창 밖이라 모든 작업자가 끝난다.
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 10, 13, 50))
    make_collector(data_dir, clock, handler, target=MIXED_TARGET).run(exit_after_window=True)

    records = _jsonl(data_dir, "2026-10-07")
    pairs = [(r["collected_at"][11:19], _target_of(r)) for r in records]
    assert pairs == [
        ("10:14:00", "900000001"),
        ("10:14:00", "900000002"),
        ("10:14:00", "arrival"),
        ("10:14:30", "900000001"),
    ]
    assert clock.now() < kst(2026, 10, 7, 10, 15)
    assert clock.alive_jobs == 0


def test_once_calls_all_targets_with_their_intervals(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 6, 11, 0, 17))  # 창 밖, 경계 아님
    outcomes = make_collector(
        data_dir, clock, handler, target=MULTI_TARGET, mode="once"
    ).poll_cycle()
    assert len(outcomes) == 4
    records = _jsonl(data_dir, "2026-10-06")
    assert [r["interval_sec"] for r in records] == [10, 30, 40, 30]
    assert handler.count_route(NIGHT_ID) == 0


# ---------------------------------------------------------------------------
# 한 대상의 지연·예외가 다른 대상에 영향을 주지 않는다
# ---------------------------------------------------------------------------
def _slow_handler(clock: FakeClock, slow_param: tuple[str, str], delay_sec: float):
    """slow_param(이름, 값) 이 있는 첫 요청만 delay_sec 걸린다(가상 시간)."""
    inner = RecordingHandler()
    state = {"is_delayed": False}

    def handler(request: httpx.Request) -> httpx.Response:
        name, value = slow_param
        if request.url.params.get(name) == value and not state["is_delayed"]:
            state["is_delayed"] = True
            clock.sleep(delay_sec)
        return inner(request)

    return handler, inner


def test_slow_arrival_does_not_delay_g1300(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # 도착 API 첫 호출이 40초 걸려도 G1300 은 10초마다, 1306·1100 도 제 시각에 부른다.
    clock = FakeClock(kst(2026, 10, 7, 5, 30))
    handler, inner = _slow_handler(clock, ("stationId", "900000105"), 40)
    with caplog.at_level(logging.WARNING, logger="app.collector.collector"):
        _run_until(data_dir, clock, handler, kst(2026, 10, 7, 5, 31, 30), target=MULTI_TARGET)

    times = _times_by_target(_jsonl(data_dir, "2026-10-07"))
    assert times[G1300_ID] == [f"05:30:{s}0" for s in range(6)] + [f"05:31:{s}0" for s in range(3)]
    assert times[R1306_ID] == ["05:30:00", "05:30:30", "05:31:00"]
    assert times[R1100_ID] == ["05:30:00", "05:30:40", "05:31:20"]
    # 도착: 05:30:00 호출이 05:30:40 에 끝나 05:30:30 주기는 건너뛰고 05:31:00 에 부른다.
    assert times["arrival"] == ["05:30:00", "05:31:00"]
    status = _status(data_dir)
    assert status["targets"]["arrival:덕현초교"]["skipped_cycles"] == 1
    assert status["targets"]["location:G1300"]["skipped_cycles"] == 0
    assert any("cycles_skipped target=arrival:덕현초교 count=1" in m for m in caplog.messages)
    assert inner.count("getBusArrivalListv2") == 2


def test_late_cycle_is_skipped_per_target_not_caught_up(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # G1300 첫 호출이 70초 걸려도 1306·도착은 05:31 에 부르고, G1300 만 05:31 을 건너뛴다.
    clock = FakeClock(kst(2026, 10, 7, 5, 30))
    handler, inner = _slow_handler(clock, ("routeId", "900000001"), 70)
    with caplog.at_level(logging.WARNING, logger="app.collector.collector"):
        _run_until(data_dir, clock, handler, kst(2026, 10, 7, 5, 32, 30))

    times = _times_by_target(_jsonl(data_dir, "2026-10-07"))
    assert times["900000001"] == ["05:30:00", "05:32:00"]
    assert times["900000002"] == ["05:30:00", "05:31:00", "05:32:00"]
    assert times["arrival"] == ["05:30:00", "05:31:00", "05:32:00"]
    assert len(inner.requests) == 8
    assert any("cycles_skipped target=location:G1300 count=1" in m for m in caplog.messages)
    targets = _status(data_dir)["targets"]
    assert targets["location:G1300"]["skipped_cycles"] == 1
    assert targets["location:1306"]["skipped_cycles"] == 0


def test_one_target_exception_does_not_affect_others(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 5, 30))
    ref: list[Collector] = []
    collector = make_collector(
        data_dir,
        clock,
        handler,
        target=MULTI_TARGET,
        sleep=_stop_at(ref, clock, kst(2026, 10, 7, 5, 31)),
    )
    ref.append(collector)
    original = collector.poll_target
    failures = {"n": 0}

    def flaky_poll_target(planned):  # 1306 의 첫 두 주기에서 예상하지 못한 예외
        if planned.key == "location:1306" and failures["n"] < 2:
            failures["n"] += 1
            raise RuntimeError("unexpected")
        return original(planned)

    collector.poll_target = flaky_poll_target  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="app.collector.collector"):
        collector.run()

    times = _times_by_target(_jsonl(data_dir, "2026-10-07"))
    assert times[G1300_ID] == [f"05:30:{s}0" for s in range(6)]
    assert R1306_ID not in times  # 05:30:00, 05:30:30 둘 다 예외
    assert times[R1100_ID] == ["05:30:00", "05:30:40"]
    assert times["arrival"] == ["05:30:00", "05:30:30"]
    assert sum("cycle_crashed target=location:1306" in m for m in caplog.messages) == 2
    assert clock.alive_jobs == 0


def test_worker_recovers_after_exception_on_next_boundary(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 5, 30))
    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, handler, sleep=_stop_at(ref, clock, kst(2026, 10, 7, 5, 31, 30))
    )
    ref.append(collector)
    original = collector.poll_target
    attempts = {"n": 0}

    def flaky_first(planned):
        attempts["n"] += 1
        if attempts["n"] == 1:  # G1300 05:30
            raise RuntimeError("unexpected")
        return original(planned)

    collector.poll_target = flaky_first  # type: ignore[method-assign]
    collector.run()

    times = _times_by_target(_jsonl(data_dir, "2026-10-07"))
    assert times["900000001"] == ["05:31:00"]
    assert times["900000002"] == ["05:30:00", "05:31:00"]
    assert times["arrival"] == ["05:30:00", "05:31:00"]


def test_stop_ends_all_workers_and_finalizes_status(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = _run_until(
        data_dir, clock, handler, kst(2026, 10, 7, 6, 0, 15), target=MULTI_TARGET
    )
    assert collector.is_stopping
    assert clock.alive_jobs == 0
    assert len(clock.finished_jobs) == 4
    # 멈춘 뒤 호출하지 않았다: 06:00:00 넷, 06:00:10 G1300.
    assert len(handler.requests) == 5
    lines = (data_dir / "2026-10-07" / "raw_poll.jsonl").read_text(encoding="utf-8")
    assert lines.endswith("\n") and len(lines.splitlines()) == 5
    assert _status(data_dir)["apis"]["buslocationservice"]["calls"] == 4
    assert clock.on_poll == collector.check_stalled  # 메인 스레드 워치독이 걸려 있다


# ---------------------------------------------------------------------------
# 워치독
# ---------------------------------------------------------------------------
def test_watchdog_logs_stalled_target_once_per_stall(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # TEST_TARGET 은 모두 60초 → 정체 기준 max(3 × 60, 60) = 180초.
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, RecordingHandler())
    g1300, r1306, _arrival = collector.planned_calls
    with caplog.at_level(logging.INFO, logger="app.collector.collector"):
        assert collector.check_stalled() == []  # 감시 시작 06:00
        collector.poll_cycle()  # 셋 다 06:00 에 시도
        clock.current = kst(2026, 10, 7, 6, 2, 30)
        collector.poll_target(g1300)
        clock.current = kst(2026, 10, 7, 6, 3, 1)
        assert collector.check_stalled() == ["location:1306", "arrival:덕현초교"]
        clock.current = kst(2026, 10, 7, 6, 3, 30)
        assert collector.check_stalled() == []  # 같은 정체 구간은 한 번만
        collector.poll_target(r1306)  # 1306 이 다시 움직인다
        assert collector.check_stalled() == []
        clock.current = kst(2026, 10, 7, 6, 6, 31)
        # G1300 은 06:02:30 부터, 1306 은 새 구간(06:03:30 부터). 도착은 아직 같은 구간.
        assert collector.check_stalled() == ["location:G1300", "location:1306"]
        clock.current = kst(2026, 10, 7, 10, 20)  # 창 밖에서는 보지 않는다
        assert collector.check_stalled() == []

    stalled = [m for m in caplog.messages if "worker_stalled" in m]
    assert len(stalled) == 4
    assert "worker_stalled target=location:1306 since=2026-10-07T06:00:00+09:00" in stalled[0]
    assert any("worker_resumed target=location:1306" in m for m in caplog.messages)
    # 기록만 한다: 작업자·호출에는 손대지 않는다.
    assert not collector.is_stopping


def test_watchdog_counts_from_window_start_for_targets_never_tried(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 5, 29))
    collector = make_collector(data_dir, clock, RecordingHandler(), target=MULTI_TARGET)
    assert collector.check_stalled() == []  # 창 밖
    clock.current = kst(2026, 10, 7, 5, 30)
    assert collector.check_stalled() == []  # 창 안을 처음 봄: 여기서부터 잰다
    clock.current = kst(2026, 10, 7, 5, 31, 1)
    # 10초(기준 60초)만 정체. 30초(90초)·40초(120초)는 아직 아니다.
    assert collector.check_stalled() == ["location:G1300"]
    clock.current = kst(2026, 10, 7, 5, 32, 1)
    assert collector.check_stalled() == ["location:1306", "location:1100", "arrival:덕현초교"]


# ---------------------------------------------------------------------------
# 빈 응답, 호출량 초과 감속
# ---------------------------------------------------------------------------
EMPTY_BODY = {"response": {"comMsgHeader": ""}}


def test_empty_response_is_counted_as_empty_not_failure(data_dir: Path) -> None:
    inner = RecordingHandler()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.params.get("routeId") == "900000002":  # 1306: 운행 안 함
            inner.requests.append(request)
            return httpx.Response(200, text=json.dumps(EMPTY_BODY))
        return inner(request)

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, handler)
    outcomes = collector.poll_cycle()
    clock.current += timedelta(minutes=1)
    collector.poll_cycle()

    assert all(o.result is not None and o.result.ok for o in outcomes)
    status = _status(data_dir)
    r1306 = status["targets"]["location:1306"]
    assert (r1306["success"], r1306["failure"], r1306["empty"]) == (2, 0, 2)
    assert r1306["consecutive_failures"] == 0
    assert status["targets"]["location:G1300"]["empty"] == 0
    location = status["apis"]["buslocationservice"]
    assert (location["failure"], location["consecutive_failures"]) == (0, 0)
    assert status["last_error"] is None
    record = next(r for r in _jsonl(data_dir, "2026-10-07") if _target_of(r) == "900000002")
    assert record["ok"] is True and record["body"] == EMPTY_BODY  # 원본 그대로
    assert record["error"] is None


def test_quota_exceeded_three_times_throttles_api_until_normal_response(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # 06:00:00 위치 3건이 모두 호출량 초과 → 위치 API 는 5분마다 시험 호출만.
    # 06:05 시험 호출도 초과, 06:10 시험 호출은 정상 → 원래 주기로 돌아간다.
    quota_xml = load_fixture("gateway_error_synthetic.xml").replace(
        "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
    )
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    inner = RecordingHandler()
    seen: list[tuple[str, str, bool]] = []  # (시각, routeId, 요청 때 status 의 throttled)

    def handler(request: httpx.Request) -> httpx.Response:
        if not request.url.path.endswith("getBusLocationListv2"):
            return inner(request)
        location = _status(data_dir)["apis"]["buslocationservice"]
        seen.append(
            (clock.now().strftime("%H:%M:%S"), request.url.params["routeId"], location["throttled"])
        )
        if clock.now() < kst(2026, 10, 7, 6, 7):
            return httpx.Response(200, text=quota_xml)
        return inner(request)

    with caplog.at_level(logging.INFO, logger="app.collector.collector"):
        _run_until(data_dir, clock, handler, kst(2026, 10, 7, 6, 10, 15), target=MULTI_TARGET)

    assert seen == [
        ("06:00:00", G1300_ID, False),
        ("06:00:00", R1306_ID, False),
        ("06:00:00", R1100_ID, False),  # 이 응답이 연속 3회째 → 감속 시작
        ("06:05:00", G1300_ID, True),  # 시험 호출(초과)
        ("06:10:00", G1300_ID, True),  # 시험 호출(정상) → 감속 해제
        ("06:10:00", R1306_ID, False),
        ("06:10:00", R1100_ID, False),
        ("06:10:10", G1300_ID, False),
    ]
    status = _status(data_dir)
    location = status["apis"]["buslocationservice"]
    assert location["throttled"] is False and location["quota_exceeded_consecutive"] == 0
    assert location["quota_exceeded"] == 4
    assert status["targets"]["location:G1300"]["throttle_skips"] > 0
    assert status["targets"]["location:1306"]["throttle_skips"] > 0
    # 도착 API 는 감속하지 않는다(06:00:00~06:10:00, 30초마다 21회).
    assert inner.count("getBusArrivalListv2") == 21
    assert status["apis"]["busarrivalservice"]["throttled"] is False
    assert any("quota_throttle_started service=buslocationservice" in m for m in caplog.messages)
    assert any("quota_throttle_ended service=buslocationservice" in m for m in caplog.messages)


def test_other_failure_does_not_reset_quota_streak(data_dir: Path) -> None:
    quota_xml = load_fixture("gateway_error_synthetic.xml").replace(
        "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
    )
    responses = iter(
        [
            httpx.Response(200, text=quota_xml),
            httpx.Response(500, text="Service Unavailable"),
            httpx.Response(200, text=quota_xml),
            httpx.Response(200, text=quota_xml),
        ]
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getBusLocationListv2"):
            return next(responses)
        return RecordingHandler()(request)

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, handler)
    g1300 = collector.planned_calls[0]
    for _ in range(4):
        collector.poll_target(g1300)
    location = _status(data_dir)["apis"]["buslocationservice"]
    assert location["quota_exceeded_consecutive"] == 3
    assert location["throttled"] is True
    assert location["next_probe_at"] == "2026-10-07T06:05:00+09:00"
    assert collector.poll_target(g1300).skipped_reason == "throttled"


# ---------------------------------------------------------------------------
# 기록·상태 파일
# ---------------------------------------------------------------------------
def test_db_failure_does_not_stop_collection(data_dir: Path) -> None:
    class BrokenSink:
        def save(self, record: dict) -> None:
            raise RuntimeError("db down")

        def close(self) -> None:
            pass

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, RecordingHandler(), db_sink=BrokenSink())
    outcomes = collector.poll_cycle()
    assert all(o.result is not None and o.result.ok for o in outcomes)
    assert raw_poll_path(data_dir, clock.now().date()).exists()
    status = _status(data_dir)
    assert status["db"] == {"enabled": True, "failures": 3}
    assert status["last_error"]["source"] == "db"
    clock.current += timedelta(minutes=1)
    assert len(collector.poll_cycle()) == 3


def test_db_save_only_for_label_targets_and_arrival(data_dir: Path) -> None:
    saved: list[str] = []

    class RecordingSink:
        def save(self, record: dict) -> None:
            saved.append(_target_of(record))

        def close(self) -> None:
            pass

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    make_collector(
        data_dir, clock, RecordingHandler(), target=MULTI_TARGET, db_sink=RecordingSink()
    ).poll_cycle()
    assert saved == [G1300_ID, R1306_ID, "arrival"]  # 1100 은 JSONL 에만
    assert len(_jsonl(data_dir, "2026-10-07")) == 4


def test_record_build_failure_is_recorded_and_next_call_continues(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_build_record = collector_module.build_record
    attempts = {"n": 0}

    def flaky_build_record(**kwargs: object) -> dict:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise ValueError("bad record")
        return real_build_record(**kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(collector_module, "build_record", flaky_build_record)
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    outcomes = make_collector(data_dir, clock, RecordingHandler()).poll_cycle()

    assert [o.skipped_reason for o in outcomes] == ["record_error", None, None]
    assert len(_jsonl(data_dir, "2026-10-07")) == 2
    status = _status(data_dir)
    assert status["apis"]["buslocationservice"]["calls"] == 2
    assert status["apis"]["buslocationservice"]["failure"] == 1
    assert status["targets"]["location:G1300"]["failure"] == 1
    assert status["last_error"]["message"].startswith("ValueError")


def test_call_count_is_saved_before_request(data_dir: Path) -> None:
    inner = RecordingHandler()
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        saved = _status(data_dir)
        service = (
            "buslocationservice"
            if request.url.path.endswith("getBusLocationListv2")
            else "busarrivalservice"
        )
        seen.append(saved["apis"][service]["calls"])
        return inner(request)

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    make_collector(data_dir, clock, handler).poll_cycle()
    assert seen == [1, 2, 1]


def test_call_count_is_saved_before_request_in_workers(data_dir: Path) -> None:
    seen: list[tuple[str, int]] = []
    inner = RecordingHandler()

    def handler(request: httpx.Request) -> httpx.Response:
        target = request.url.params.get("routeId", "arrival")
        seen.append((target, _status(data_dir)["targets"]["location:G1300"]["calls"]))
        return inner(request)

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    _run_until(data_dir, clock, handler, kst(2026, 10, 7, 6, 0, 15), target=MULTI_TARGET)
    g1300 = [count for target, count in seen if target == G1300_ID]
    assert g1300 == [1, 2]


def test_quota_exceeded_response_is_reported(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    quota_xml = (
        load_fixture("gateway_error_synthetic.xml")
        .replace(
            "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
            "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
        )
        .replace(
            "<returnReasonCode>30</returnReasonCode>", "<returnReasonCode>22</returnReasonCode>"
        )
    )
    inner = RecordingHandler()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("getBusLocationListv2"):
            inner.requests.append(request)
            return httpx.Response(200, text=quota_xml)
        return inner(request)

    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, handler)
    with caplog.at_level(logging.ERROR, logger="app.collector.collector"):
        outcomes = collector.poll_cycle()
        clock.current += timedelta(minutes=1)
        second = collector.poll_cycle()

    assert [o.result.is_quota_exceeded for o in outcomes if o.result] == [True, True, False]
    # 06:01 G1300 이 연속 3회째 → 감속 시작, 같은 주기의 1306 은 시험 호출 시각 전이라 건너뜀.
    assert [o.skipped_reason for o in second] == [None, "throttled", None]
    location = _status(data_dir)["apis"]["buslocationservice"]
    assert location["quota_exceeded"] == 3
    assert location["throttled"] is True
    assert location["quota_exceeded_at"] == "2026-10-07T06:00:00+09:00"
    loud = [m for m in caplog.messages if "GBIS_DAILY_QUOTA_EXCEEDED" in m]
    assert len(loud) == 1 and "service=buslocationservice" in loud[0]
    record = _jsonl(data_dir, "2026-10-07")[0]
    assert record["ok"] is False and "하루 호출량 초과" in record["error"]
