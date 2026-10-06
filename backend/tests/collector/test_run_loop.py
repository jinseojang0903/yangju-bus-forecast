import json
import logging
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

from app.collector import collector as collector_module
from app.collector.collector import Collector
from app.collector.storage import raw_poll_path
from tests.collector.helpers import (
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


def test_exit_after_window_stops_at_10(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 9, 57, 30))
    collector = make_collector(data_dir, clock, handler)
    collector.run(exit_after_window=True)

    records = _jsonl(data_dir, "2026-10-07")
    assert [r["collected_at"][11:19] for r in records] == ["09:58:00"] * 3 + ["09:59:00"] * 3
    assert {r["mode"] for r in records} == {"run"}
    assert clock.now() < kst(2026, 10, 7, 10, 1)
    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["in_window"] is False


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


def _stop_at(collector_ref: list[Collector], clock: FakeClock, stop_at):
    def sleep(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now() >= stop_at:
            collector_ref[0].stop()

    return sleep


def test_run_waits_for_window_and_aligns_to_minute(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 4, 58, 17))
    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, handler, sleep=_stop_at(ref, clock, kst(2026, 10, 7, 5, 2))
    )
    ref.append(collector)
    collector.run()

    records = _jsonl(data_dir, "2026-10-07")
    assert [r["collected_at"][11:23] for r in records] == (
        ["05:00:00.000"] * 3 + ["05:01:00.000"] * 3
    )


def test_late_cycle_is_skipped_not_caught_up(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    clock = FakeClock(kst(2026, 10, 7, 5, 0))
    calls = {"n": 0}

    def slow_first_call(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            clock.sleep(70)  # 첫 호출이 70초 걸렸다
        name = (
            "bus_location_ok_synthetic.json"
            if request.url.path.endswith("getBusLocationListv2")
            else "bus_arrival_ok_synthetic.json"
        )
        return httpx.Response(200, text=load_fixture(name))

    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, slow_first_call, sleep=_stop_at(ref, clock, kst(2026, 10, 7, 5, 2, 30))
    )
    ref.append(collector)
    with caplog.at_level(logging.WARNING, logger="app.collector.collector"):
        collector.run()

    minutes = [r["collected_at"][11:16] for r in _jsonl(data_dir, "2026-10-07")]
    # 05:00 주기(늦게 끝남) → 05:01 주기는 건너뜀 → 05:02 주기
    assert minutes[:3] == ["05:00", "05:01", "05:01"]
    assert minutes[3:] == ["05:02"] * 3
    assert calls["n"] == 6
    assert any("cycles_skipped count=1" in m for m in caplog.messages)


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
    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["db"] == {"enabled": True, "failures": 3}
    assert status["last_error"]["source"] == "db"
    clock.current += timedelta(minutes=1)
    assert len(collector.poll_cycle()) == 3


def _minutes(data_dir: Path, day: str) -> list[str]:
    return [r["collected_at"][11:16] for r in _jsonl(data_dir, day)]


def test_wake_late_from_window_wait_still_polls_0500_and_every_minute(data_dir: Path) -> None:
    # sleep 이 매번 3ms 늦게 깨어나도 05:00 주기를 건너뛰지 않고, 매분 빠짐없이 호출한다.
    handler = RecordingHandler()
    clock = OvershootClock(kst(2026, 10, 7, 4, 55))
    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, handler, sleep=_stop_at(ref, clock, kst(2026, 10, 7, 5, 5, 30))
    )
    ref.append(collector)
    collector.run(exit_after_window=True)

    records = _jsonl(data_dir, "2026-10-07")
    assert records[0]["collected_at"].startswith("2026-10-07T05:00:00")
    expected = [f"05:0{m}" for m in range(6) for _ in range(3)]
    assert _minutes(data_dir, "2026-10-07") == expected
    assert handler.count("getBusLocationListv2") == 12
    assert handler.count("getBusArrivalListv2") == 6


def test_process_running_overnight_polls_0500_next_day(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = OvershootClock(kst(2026, 10, 7, 10, 0, 30))
    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, handler, sleep=_stop_at(ref, clock, kst(2026, 10, 8, 5, 1, 30))
    )
    ref.append(collector)
    collector.run()

    assert _minutes(data_dir, "2026-10-08") == ["05:00"] * 3 + ["05:01"] * 3
    assert not (data_dir / "2026-10-07").exists()


def test_cycle_exception_does_not_stop_run(
    data_dir: Path, caplog: pytest.LogCaptureFixture
) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 5, 0))
    ref: list[Collector] = []
    collector = make_collector(
        data_dir, clock, handler, sleep=_stop_at(ref, clock, kst(2026, 10, 7, 5, 1, 30))
    )
    ref.append(collector)
    original = collector.poll_cycle
    attempts = {"n": 0}

    def flaky_cycle() -> list:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise RuntimeError("unexpected")
        return original()

    collector.poll_cycle = flaky_cycle  # type: ignore[method-assign]
    with caplog.at_level(logging.ERROR, logger="app.collector.collector"):
        collector.run()

    assert attempts["n"] == 2
    assert _minutes(data_dir, "2026-10-07") == ["05:01"] * 3
    assert any("cycle_crashed" in m for m in caplog.messages)


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
    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["apis"]["buslocationservice"]["calls"] == 2
    assert status["apis"]["buslocationservice"]["failure"] == 1
    assert status["last_error"]["message"].startswith("ValueError")


def test_call_count_is_saved_before_request(data_dir: Path) -> None:
    inner = RecordingHandler()
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        saved = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
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
