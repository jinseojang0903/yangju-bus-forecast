import json
from pathlib import Path

from app.core.settings import CALL_LIMITS, CallLimits
from tests.collector.helpers import FakeClock, RecordingHandler, kst, make_collector

LOCATION = "buslocationservice"
ARRIVAL = "busarrivalservice"
SMALL_LIMITS = CallLimits(daily_limit_per_api=5, daily_safe_limit_per_api=3)


def _status(data_dir: Path) -> dict:
    return json.loads((data_dir / "status.json").read_text(encoding="utf-8"))


def test_default_limits_are_fixed() -> None:
    assert CALL_LIMITS.daily_limit_per_api == 1000
    assert CALL_LIMITS.daily_safe_limit_per_api == 980


def test_one_cycle_makes_three_calls(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    outcomes = make_collector(data_dir, clock, handler).poll_cycle()

    assert handler.count("getBusLocationListv2") == 2
    assert handler.count("getBusArrivalListv2") == 1
    assert all(o.result is not None and o.result.ok for o in outcomes)
    status = _status(data_dir)
    assert status["apis"][LOCATION]["calls"] == 2
    assert status["apis"][ARRIVAL]["success"] == 1
    assert status["last_success_at"] == "2026-10-07T06:00:00+09:00"


def test_cap_stops_calls_and_survives_restart(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 6, 0))

    first = make_collector(data_dir, clock, handler, limits=SMALL_LIMITS)
    first.poll_cycle()  # 위치 2, 도착 1
    outcomes = first.poll_cycle()  # 위치 1 더(=3) 뒤 상한, 도착 2
    assert handler.count("getBusLocationListv2") == 3
    assert [o.skipped_reason for o in outcomes] == [None, "daily_cap", None]
    status = _status(data_dir)
    assert status["apis"][LOCATION]["calls"] == 3
    assert status["apis"][LOCATION]["capped"] is True

    # 재시작: 새 인스턴스가 상태 파일에서 이어 센다.
    second = make_collector(data_dir, clock, handler, limits=SMALL_LIMITS)
    assert second.status.calls(LOCATION) == 3
    second.poll_cycle()  # 위치 0, 도착 3
    second.poll_cycle()  # 위치 0, 도착 0
    assert handler.count("getBusLocationListv2") == 3
    assert handler.count("getBusArrivalListv2") == 3
    status = _status(data_dir)
    assert status["apis"][ARRIVAL]["calls"] == 3
    assert status["apis"][ARRIVAL]["capped"] is True


def test_counts_reset_on_new_kst_day(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 9, 59))
    collector = make_collector(data_dir, clock, handler, limits=SMALL_LIMITS)
    collector.poll_cycle()
    collector.poll_cycle()

    clock.current = kst(2026, 10, 8, 5, 0)
    collector.poll_cycle()
    status = _status(data_dir)
    assert status["date"] == "2026-10-08"
    assert status["apis"][LOCATION]["calls"] == 2
    assert status["apis"][LOCATION]["capped"] is False

    # 다음 날 재시작해도 그날 수를 이어 센다.
    restarted = make_collector(data_dir, clock, handler, limits=SMALL_LIMITS)
    assert restarted.status.calls(LOCATION) == 2
