import json
from pathlib import Path

import pytest

from app.collector import storage
from app.collector.lock import AlreadyRunningError, collector_lock, is_collector_running
from app.collector.redact import Redactor
from app.collector.status import build_status_report
from app.collector.storage import write_json_atomic
from tests.collector.helpers import FakeClock, RecordingHandler, kst, make_collector


def test_atomic_write_replaces_and_leaves_no_temp(tmp_path: Path) -> None:
    target = tmp_path / "status.json"
    write_json_atomic(target, {"n": 1}, Redactor())
    write_json_atomic(target, {"n": 2}, Redactor())
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 2}
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]


def test_atomic_write_failure_keeps_previous_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "status.json"
    write_json_atomic(target, {"n": 1}, Redactor())

    def broken_replace(src: object, dst: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(storage.os, "replace", broken_replace)
    with pytest.raises(OSError):
        write_json_atomic(target, {"n": 2}, Redactor())
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["status.json"]


def test_atomic_write_retries_when_file_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "status.json"
    real_replace = storage.os.replace
    attempts = {"n": 0}

    def flaky_replace(src: str, dst: str) -> None:
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise PermissionError("busy")
        real_replace(src, dst)

    monkeypatch.setattr(storage.os, "replace", flaky_replace)
    monkeypatch.setattr(storage.time, "sleep", lambda _: None)
    write_json_atomic(target, {"n": 3}, Redactor())
    assert attempts["n"] == 3
    assert json.loads(target.read_text(encoding="utf-8")) == {"n": 3}


def test_lock_prevents_second_instance(data_dir: Path) -> None:
    assert is_collector_running(data_dir) is False
    with collector_lock(data_dir):
        assert is_collector_running(data_dir) is True
        with pytest.raises(AlreadyRunningError):
            with collector_lock(data_dir):
                pass
    assert is_collector_running(data_dir) is False
    with collector_lock(data_dir):
        pass


def test_status_report_is_one_line_json(data_dir: Path) -> None:
    clock = FakeClock(kst(2026, 10, 7, 6, 0))
    missing = build_status_report(data_dir, clock.now(), is_running=False)
    assert missing["status_file"] == "missing"
    assert missing["jsonl_lines_today"] == 0

    make_collector(data_dir, clock, RecordingHandler()).poll_cycle()
    report = build_status_report(data_dir, clock.now(), is_running=True)
    line = json.dumps(report, ensure_ascii=False, separators=(",", ":"))
    assert "\n" not in line
    assert report["status_file"] == "today"
    assert report["in_window_now"] is True and report["in_window"] is True
    assert report["jsonl_lines_today"] == 3
    assert report["apis"]["buslocationservice"]["calls"] == 2
    assert report["last_success_age_sec"] == 0
    assert report["running"] is True

    stale = build_status_report(data_dir, kst(2026, 10, 8, 6, 0), is_running=False)
    assert stale["status_file"] == "stale"
