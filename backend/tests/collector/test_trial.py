"""시운전(run --trial-until HH:MM): 창 밖에서도 그 시각 미만까지 1분마다, mode=trial."""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from app.collector import cli
from app.collector.collector import MODE_TRIAL
from app.collector.redact import Redactor
from app.core.settings import COLLECT_TARGET, CallLimits
from tests.collector.helpers import (
    MIXED_TARGET,
    FakeClock,
    OvershootClock,
    RecordingHandler,
    kst,
    make_collector,
    make_settings,
)

# 2026-10-06 화요일 11:00 은 수집 창(평일 05:30~10:15) 밖이다.
TUESDAY_1100 = kst(2026, 10, 6, 11, 0)


def _jsonl(data_dir: Path) -> list[dict]:
    path = data_dir / "2026-10-06" / "raw_poll.jsonl"
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


@pytest.mark.parametrize("clock_cls", [FakeClock, OvershootClock], ids=["exact", "late-3ms"])
def test_trial_outside_window_polls_until_before_end_time(data_dir: Path, clock_cls) -> None:
    handler = RecordingHandler()
    clock = clock_cls(TUESDAY_1100)
    collector = make_collector(data_dir, clock, handler, mode=MODE_TRIAL)
    collector.run_trial(kst(2026, 10, 6, 11, 3))

    records = _jsonl(data_dir)
    # 11:03 은 포함하지 않는다('그 시각 미만').
    assert [r["collected_at"][11:16] for r in records] == (
        ["11:00"] * 3 + ["11:01"] * 3 + ["11:02"] * 3
    )
    assert {r["mode"] for r in records} == {"trial"}
    assert {r["is_weekday"] for r in records} == {True}
    assert handler.count("getBusLocationListv2") == 6
    assert handler.count("getBusArrivalListv2") == 3
    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["mode"] == "trial"
    assert status["apis"]["buslocationservice"]["calls"] == 6
    assert clock.now() < kst(2026, 10, 6, 11, 3)


def test_trial_respects_call_cap(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(TUESDAY_1100)
    small = CallLimits(daily_limit_per_api=5, daily_safe_limit_per_api=3)
    make_collector(data_dir, clock, handler, mode=MODE_TRIAL, limits=small).run_trial(
        kst(2026, 10, 6, 11, 5)
    )
    assert handler.count("getBusLocationListv2") == 3


def test_resolve_trial_until() -> None:
    assert cli.resolve_trial_until("11:03", TUESDAY_1100) == kst(2026, 10, 6, 11, 3)
    assert cli.resolve_trial_until(" 23:59 ", TUESDAY_1100) == kst(2026, 10, 6, 23, 59)


@pytest.mark.parametrize("value", ["10:59", "11:00", "00:00"], ids=["past", "now", "midnight"])
def test_trial_until_past_exits_2_without_calls(tmp_path: Path, value: str) -> None:
    settings = make_settings(collect_data_dir=str(tmp_path))
    code = cli.cmd_run(
        settings,
        Redactor(settings.secret_values()),
        exit_after_window=False,
        trial_until=value,
        clock=FakeClock(TUESDAY_1100),
    )
    assert code == 2
    assert list(tmp_path.iterdir()) == []  # 잠금·상태 파일도 만들지 않았다


@pytest.mark.parametrize("value", ["1103", "24:00", "11:60", "1:03", "ab:cd", ""])
def test_trial_until_bad_format_exits_2(tmp_path: Path, value: str) -> None:
    settings = make_settings(collect_data_dir=str(tmp_path))
    code = cli.cmd_run(
        settings,
        Redactor(settings.secret_values()),
        exit_after_window=False,
        trial_until=value,
        clock=FakeClock(TUESDAY_1100),
    )
    assert code == 2
    assert list(tmp_path.iterdir()) == []


def test_parser_accepts_trial_until_and_rejects_both_modes() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["run", "--trial-until", "11:30"])
    assert args.trial_until == "11:30" and args.exit_after_window is False
    with pytest.raises(SystemExit):
        parser.parse_args(["run", "--trial-until", "11:30", "--exit-after-window"])
    args = parser.parse_args(["run", "--trial-until", "11:30", "--interval-sec", "40"])
    assert args.interval_sec == 40
    assert parser.parse_args(["run"]).interval_sec is None


# ---------------------------------------------------------------------------
# --interval-sec (시운전 전용)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("clock_cls", [FakeClock, OvershootClock], ids=["exact", "late-3ms"])
def test_trial_40_second_interval(data_dir: Path, clock_cls) -> None:
    handler = RecordingHandler()
    clock = clock_cls(kst(2026, 10, 6, 10, 20))
    collector = make_collector(data_dir, clock, handler, mode=MODE_TRIAL, interval_sec=40)
    collector.run_trial(kst(2026, 10, 6, 10, 22))

    records = _jsonl(data_dir)
    starts = [r["collected_at"][11:19] for r in records]
    # 10:22:00 은 포함하지 않는다('그 시각 미만').
    assert starts == ["10:20:00"] * 3 + ["10:20:40"] * 3 + ["10:21:20"] * 3
    assert {r["interval_sec"] for r in records} == {40}
    assert {r["mode"] for r in records} == {"trial"}
    status = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert status["interval_sec"] == 40


def test_formal_run_uses_per_target_intervals(data_dir: Path) -> None:
    # 시운전 옵션 없이 정식 run 은 대상별 주기(60초 대상만이면 60초 경계)를 쓴다.
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 7, 10, 12, 30))
    make_collector(data_dir, clock, handler).run(exit_after_window=True)

    path = data_dir / "2026-10-07" / "raw_poll.jsonl"
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [r["collected_at"][11:19] for r in records] == ["10:13:00"] * 3 + ["10:14:00"] * 3
    assert {r["interval_sec"] for r in records} == {60}
    assert {r["mode"] for r in records} == {"run"}


def test_trial_interval_override_applies_to_mixed_targets(data_dir: Path) -> None:
    # --interval-sec 를 쓰면 대상별 주기와 상관없이 모두 그 간격으로 부른다.
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 6, 10, 20))
    collector = make_collector(
        data_dir, clock, handler, mode=MODE_TRIAL, interval_sec=40, target=MIXED_TARGET
    )
    collector.run_trial(kst(2026, 10, 6, 10, 21))
    records = _jsonl(data_dir)
    assert len(records) == 6  # 10:20:00, 10:20:40 × 3
    assert {r["interval_sec"] for r in records} == {40}


def test_trial_without_interval_uses_per_target_intervals(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 6, 11, 0))
    collector = make_collector(data_dir, clock, handler, mode=MODE_TRIAL, target=MIXED_TARGET)
    collector.run_trial(kst(2026, 10, 6, 11, 1))
    starts = [r["collected_at"][11:19] for r in _jsonl(data_dir)]
    assert starts == ["11:00:00"] * 3 + ["11:00:30"]


def test_formal_run_rejected_when_planned_calls_exceed_950(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 설정 주입: G1300·1306 을 10초로 하면 위치 API 하루 예상 1710+1710 회 > 950.
    heavy = replace(
        COLLECT_TARGET,
        routes=tuple(replace(r, interval_sec=10) for r in COLLECT_TARGET.routes),
    )
    monkeypatch.setattr(cli, "COLLECT_TARGET", heavy)
    assert _cmd_run(tmp_path, exit_after_window=False) == 2
    assert list(tmp_path.iterdir()) == []


def _cmd_run(tmp_path: Path, **kwargs: object) -> int:
    settings = make_settings(collect_data_dir=str(tmp_path))
    return cli.cmd_run(
        settings,
        Redactor(settings.secret_values()),
        clock=FakeClock(TUESDAY_1100),
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.parametrize(
    "kwargs",
    [
        {"exit_after_window": False, "interval_sec": 40},  # 단독
        {"exit_after_window": True, "interval_sec": 40},  # 정식 수집과 함께
        {"exit_after_window": False, "trial_until": "11:30", "interval_sec": 35},
        {"exit_after_window": False, "trial_until": "11:30", "interval_sec": 9},
        {"exit_after_window": False, "trial_until": "11:30", "interval_sec": 61},
    ],
    ids=["alone", "with-exit-after-window", "35-not-divisor", "9-too-small", "61-too-large"],
)
def test_interval_sec_rejected_exits_2_without_calls(tmp_path: Path, kwargs: dict) -> None:
    assert _cmd_run(tmp_path, **kwargs) == 2
    assert list(tmp_path.iterdir()) == []


# ---------------------------------------------------------------------------
# --only-route / --skip-arrival (시운전 전용)
# ---------------------------------------------------------------------------
def test_trial_10_seconds_only_g1300_without_arrival(data_dir: Path) -> None:
    handler = RecordingHandler()
    clock = FakeClock(kst(2026, 10, 6, 10, 30))
    target = cli.trial_target(["G1300"], skip_arrival=True)
    collector = make_collector(
        data_dir, clock, handler, mode=MODE_TRIAL, interval_sec=10, target=target
    )
    collector.run_trial(kst(2026, 10, 6, 10, 30, 30))

    assert handler.count("getBusArrivalListv2") == 0
    route_ids = [r.url.params["routeId"] for r in handler.requests]
    g1300_id = COLLECT_TARGET.routes[0].route_id
    assert route_ids == [g1300_id] * 3
    records = _jsonl(data_dir)
    assert [r["collected_at"][11:19] for r in records] == ["10:30:00", "10:30:10", "10:30:20"]
    assert {r["interval_sec"] for r in records} == {10}
    assert {r["mode"] for r in records} == {"trial"}


def test_trial_target_options() -> None:
    assert cli.trial_target(None, skip_arrival=False) is COLLECT_TARGET
    both = cli.trial_target(["1306", "G1300"], skip_arrival=False)
    assert [r.route_name for r in both.routes] == ["G1300", "1306"]
    assert both.board_station_id == COLLECT_TARGET.board_station_id
    no_arrival = cli.trial_target(None, skip_arrival=True)
    assert no_arrival.board_station_id is None
    assert no_arrival.routes == COLLECT_TARGET.routes
    with pytest.raises(ValueError):
        cli.trial_target(["G9999"], skip_arrival=False)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"exit_after_window": False, "trial_until": "11:30", "only_routes": ["G9999"]},
        {"exit_after_window": False, "only_routes": ["G1300"]},  # 단독
        {"exit_after_window": False, "skip_arrival": True},  # 단독
        {"exit_after_window": True, "only_routes": ["G1300"]},  # 정식 수집과 함께
    ],
    ids=["unknown-route", "only-route-alone", "skip-arrival-alone", "with-exit-after-window"],
)
def test_target_options_rejected_exits_2_without_calls(tmp_path: Path, kwargs: dict) -> None:
    assert _cmd_run(tmp_path, **kwargs) == 2
    assert list(tmp_path.iterdir()) == []


def test_parser_accepts_target_options() -> None:
    args = cli.build_parser().parse_args(
        [
            "run",
            "--trial-until",
            "10:40",
            "--interval-sec",
            "10",
            "--only-route",
            "G1300",
            "--only-route",
            "1306",
            "--skip-arrival",
        ]
    )
    assert args.only_route == ["G1300", "1306"]
    assert args.skip_arrival is True and args.interval_sec == 10
    plain = cli.build_parser().parse_args(["run"])
    assert plain.only_route is None and plain.skip_arrival is False
