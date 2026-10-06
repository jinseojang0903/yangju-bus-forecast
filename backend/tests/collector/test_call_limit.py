import json
from pathlib import Path

from app.core.settings import CALL_LIMITS
from tests.collector.helpers import (
    MULTI_TARGET,
    SMALL_LIMITS,
    FakeClock,
    RecordingHandler,
    kst,
    make_collector,
)

LOCATION = "buslocationservice"
ARRIVAL = "busarrivalservice"


def _status(data_dir: Path) -> dict:
    return json.loads((data_dir / "status.json").read_text(encoding="utf-8"))


def test_default_limits_are_per_api() -> None:
    # 사용자 결정(2026-10-06): 위치는 운영계정 10,000회, 도착은 1,000회.
    location = CALL_LIMITS.quota_for(LOCATION)
    assert (location.daily_limit, location.safe_limit, location.planned_max) == (
        10_000,
        9_800,
        9_000,
    )
    arrival = CALL_LIMITS.quota_for(ARRIVAL)
    assert (arrival.daily_limit, arrival.safe_limit, arrival.planned_max) == (1_000, 980, 950)
    assert CALL_LIMITS.safe_limit_for("busrouteservice") == 4
    assert CALL_LIMITS.as_dict()[LOCATION] == {
        "daily_limit": 10_000,
        "safe_limit": 9_800,
        "planned_max": 9_000,
    }


def test_cap_stops_all_workers_of_that_api(data_dir: Path) -> None:
    # 위치 안전 상한 3회: 05:30:00 의 G1300·1306·1100 다음부터 위치 작업자는 모두 멈추고
    # 도착 작업자만 계속한다(도착도 3회에서 멈춘다).
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 5, 30))
    collector = make_collector(
        data_dir, clock, handler, target=MULTI_TARGET, limits=SMALL_LIMITS, mode="run"
    )

    def stop_after(seconds: float) -> None:
        clock.sleep(seconds)
        if clock.now() >= kst(2026, 10, 7, 5, 32):
            collector.stop()

    collector._sleep = stop_after  # type: ignore[method-assign]
    collector.run()

    assert handler.count("getBusLocationListv2") == 3
    assert handler.count("getBusArrivalListv2") == 3
    status = _status(data_dir)
    assert status["apis"][LOCATION]["capped"] is True
    assert status["apis"][ARRIVAL]["capped"] is True
    assert status["targets"]["location:G1300"]["cap_skips"] > 0
    assert status["targets"]["location:1100"]["calls"] == 1


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
