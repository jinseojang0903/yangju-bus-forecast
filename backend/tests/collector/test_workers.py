"""실제 스레드(ThreadRunner)로 공유 자원(JSONL·상태 파일) 잠금과 종료를 확인한다.

시각은 움직이지 않는 FakeClock 이라 잠자기 없이 동시에 호출만 겹친다.
"""

import json
import threading
import time
from pathlib import Path

from app.collector.collector import ThreadRunner
from app.collector.status import build_status_report
from tests.collector.helpers import MULTI_TARGET, FakeClock, RecordingHandler, kst, make_collector

CALLS_PER_WORKER = 5


def test_concurrent_workers_write_whole_lines_and_exact_counts(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    collector = make_collector(data_dir, clock, handler, target=MULTI_TARGET)
    barrier = threading.Barrier(len(collector.planned_calls))

    def job(planned):
        def run() -> None:
            barrier.wait(5)  # 모두 같은 순간에 시작한다
            for _ in range(CALLS_PER_WORKER):
                collector.poll_target(planned)

        return run

    ThreadRunner(join_poll_sec=0.01).run_all([(p.key, job(p)) for p in collector.planned_calls])
    collector.close()

    lines = (data_dir / "2026-10-07" / "raw_poll.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 4 * CALLS_PER_WORKER
    records = [json.loads(line) for line in lines]  # 줄이 섞이거나 깨지지 않았다
    assert {r["api"] for r in records} == {"getBusLocationListv2", "getBusArrivalListv2"}

    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["apis"]["buslocationservice"]["calls"] == 3 * CALLS_PER_WORKER
    assert status["apis"]["busarrivalservice"]["calls"] == CALLS_PER_WORKER
    assert {k: t["calls"] for k, t in status["targets"].items()} == {
        "location:G1300": CALLS_PER_WORKER,
        "location:1306": CALLS_PER_WORKER,
        "location:1100": CALLS_PER_WORKER,
        "arrival:덕현초교": CALLS_PER_WORKER,
    }
    assert [p.name for p in data_dir.iterdir() if p.name.endswith(".tmp")] == []


def test_thread_runner_returns_after_stop() -> None:
    stop = threading.Event()
    finished: list[str] = []
    lock = threading.Lock()

    def job(name: str):
        def run() -> None:
            stop.wait(10)
            with lock:
                finished.append(name)

        return run

    timer = threading.Timer(0.05, stop.set)
    timer.start()
    started = time.monotonic()
    ThreadRunner(join_poll_sec=0.01).run_all([(f"w{i}", job(f"w{i}")) for i in range(3)])
    assert sorted(finished) == ["w0", "w1", "w2"]
    assert time.monotonic() - started < 5


def test_thread_runner_calls_watchdog_while_waiting() -> None:
    stop = threading.Event()
    polls = {"n": 0}

    def on_poll() -> None:
        polls["n"] += 1
        if polls["n"] == 2:
            raise RuntimeError("watchdog bug")  # 워치독 오류가 기다림을 끊지 않는다
        if polls["n"] >= 3:
            stop.set()

    ThreadRunner(join_poll_sec=0.01).run_all([("w", lambda: stop.wait(10))], on_poll=on_poll)
    assert polls["n"] >= 3


def test_status_report_shows_targets_and_api_plan(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    make_collector(data_dir, clock, RecordingHandler(), target=MULTI_TARGET).poll_cycle()
    report = build_status_report(data_dir, clock.now(), is_running=True)

    config = report["config"]
    assert config["window"] == "05:30-10:15"
    assert config["intervals"]["location:G1300"] == 10
    assert config["apis"]["buslocationservice"] == {
        "planned": 6560,
        "daily_limit": 10_000,
        "safe_limit": 9_800,
        "planned_max": 9_000,
    }
    assert config["apis"]["busarrivalservice"] == {
        "planned": 570,
        "daily_limit": 1_000,
        "safe_limit": 980,
        "planned_max": 950,
    }
    assert "G1300N" in config["not_collected"]
    g1300 = report["targets"]["location:G1300"]
    assert (g1300["interval_sec"], g1300["calls"], g1300["success"], g1300["failure"]) == (
        10,
        1,
        1,
        0,
    )
    assert g1300["skipped_cycles"] == 0
    assert g1300["last_success_at"] == "2026-10-07T06:00:00+09:00"
    assert report["limits"]["buslocationservice"]["safe_limit"] == 9_800
