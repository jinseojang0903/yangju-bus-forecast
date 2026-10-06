"""상태 파일이 깨지거나 없어져도 오늘 호출 수를 JSONL 에서 복구한다."""

import json
from datetime import timedelta
from pathlib import Path

from app.collector.status import recover_counts
from app.collector.storage import raw_poll_path
from tests.collector.helpers import SMALL_LIMITS, FakeClock, RecordingHandler, kst, make_collector

LOCATION = "buslocationservice"
ARRIVAL = "busarrivalservice"


def _two_cycles(data_dir: Path, clock: FakeClock, handler: RecordingHandler) -> None:
    collector = make_collector(data_dir, clock, handler)
    collector.poll_cycle()
    clock.current += timedelta(minutes=1)
    collector.poll_cycle()


def test_corrupt_status_recovers_counts_from_jsonl(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    handler = RecordingHandler()
    _two_cycles(data_dir, clock, handler)
    (data_dir / "status.json").write_text("{broken", encoding="utf-8")

    restarted = make_collector(data_dir, clock, handler)
    assert restarted.status.calls(LOCATION) == 4
    assert restarted.status.calls(ARRIVAL) == 2
    assert restarted.status.api(LOCATION)["success"] == 4
    assert restarted.status.data["last_success_at"] == "2026-10-07T06:01:00+09:00"


def test_missing_status_with_partial_last_line_counts_it_as_failure(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    handler = RecordingHandler()
    _two_cycles(data_dir, clock, handler)
    (data_dir / "status.json").unlink()
    jsonl = raw_poll_path(data_dir, clock.now().date())
    with jsonl.open("a", encoding="utf-8") as fp:
        fp.write('{"collected_at":"2026-10-07T06:02:00.000+09:00","api":"getBusLocationListv2","pa')

    recovered = recover_counts(jsonl)
    assert recovered.unreadable_lines == 1
    assert recovered.counters[LOCATION]["calls"] == 5
    assert recovered.counters[LOCATION]["failure"] == 1
    assert recovered.counters[LOCATION]["consecutive_failures"] == 1

    restarted = make_collector(data_dir, clock, handler)
    assert restarted.status.calls(LOCATION) == 5

    # 깨진 줄 뒤에 새 줄을 이어 붙이지 않고 다음 줄부터 쓴다.
    clock.current += timedelta(minutes=2)
    restarted.poll_cycle()
    lines = jsonl.read_text(encoding="utf-8").splitlines()
    assert lines[-4].endswith('"pa')
    assert [json.loads(line)["api"] for line in lines[-3:]] == [
        "getBusLocationListv2",
        "getBusLocationListv2",
        "getBusArrivalListv2",
    ]


def test_recovered_counts_still_enforce_cap(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    handler = RecordingHandler()
    _two_cycles(data_dir, clock, handler)  # 위치 4, 도착 2
    (data_dir / "status.json").write_text("not json", encoding="utf-8")

    restarted = make_collector(data_dir, clock, handler, limits=SMALL_LIMITS)
    before = handler.count("getBusLocationListv2")
    outcomes = restarted.poll_cycle()
    assert handler.count("getBusLocationListv2") == before
    assert [o.skipped_reason for o in outcomes] == ["daily_cap", "daily_cap", None]


def test_no_jsonl_today_starts_from_zero(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    handler = RecordingHandler()
    _two_cycles(data_dir, clock, handler)

    clock.current = kst(2026, 10, 8, 6, 0)
    next_day = make_collector(data_dir, clock, handler)
    assert next_day.status.calls(LOCATION) == 0
