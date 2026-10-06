"""시운전(run --trial-until HH:MM): 창 밖에서도 그 시각 미만까지 1분마다, mode=trial."""

import json
from pathlib import Path

import pytest

from app.collector import cli
from app.collector.collector import MODE_TRIAL
from app.collector.redact import Redactor
from app.core.settings import CallLimits
from tests.collector.helpers import (
    FakeClock,
    OvershootClock,
    RecordingHandler,
    kst,
    make_collector,
    make_settings,
)

# 2026-10-06 화요일 11:00 은 수집 창(평일 05~10시) 밖이다.
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
