"""python -m app.loader: 요약 출력, 종료 코드(0·1·2·3), dry-run, 가짜 저장소 적재."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from app.core.settings import Settings
from app.loader import cli
from tests.loader.fakes import FIXTURE_DATA_DIR, FakeStore

DATA_DIR = str(FIXTURE_DATA_DIR)


class Captured:
    def __init__(self) -> None:
        self.out: list[str] = []
        self.err: list[str] = []

    @property
    def text(self) -> str:
        return "\n".join(self.out)


def run(argv: list[str], store: FakeStore | None = None, **kwargs) -> tuple[int, Captured]:
    captured = Captured()
    factory = (lambda: store) if store is not None else kwargs.pop("store_factory", None)
    code = cli.main(
        argv,
        settings=kwargs.pop("settings", Settings(_env_file=None, database_url="")),
        store_factory=factory,
        out=captured.out.append,
        err=captured.err.append,
    )
    return code, captured


def test_dry_run_prints_summary_without_db() -> None:
    code, captured = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR, "--dry-run"])
    assert code == cli.EXIT_OK
    text = captured.text
    assert "== 2026-10-07 (2026-10-07/raw_poll.jsonl) ==" in text
    assert "읽은 줄        14" in text
    assert "대상 줄        8 (1306 1, G1300 5, 도착 2; 그중 ok=false 1)" in text
    assert "건너뛴 줄      6 (다른 노선·정류장·API 1, 깨진 줄 3, 빈 줄 1," in text
    assert "raw_poll       넣을 줄 8 (DB 확인 안 함)" in text
    assert "bus_position   행 5" in text
    assert "bus_arrival    행 3 (차 정보 없는 순위 1, 대상 밖 노선 항목 1)" in text
    assert "service_day    평일 예, 공휴일 아니오, 운영 regular" in text
    assert "dry-run" in text


def test_dry_run_needs_no_database_url() -> None:
    # DB 미설정이어도 dry-run 은 돈다(종료 코드 2 가 아님).
    code, _ = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR, "--dry-run"])
    assert code == cli.EXIT_OK


def test_load_twice_reports_existing_rows() -> None:
    store = FakeStore()
    code, captured = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR], store)
    assert code == cli.EXIT_OK
    assert "raw_poll       새로 8, 이미 있음 0" in captured.text
    assert "bus_position   행 5, 새로 5" in captured.text
    assert store.closed is True

    code, captured = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR], store)
    assert code == cli.EXIT_OK
    assert "raw_poll       새로 0, 이미 있음 8" in captured.text
    assert "bus_arrival    행 3, 새로 0" in captured.text
    assert store.counts() == {"service_day": 1, "raw_poll": 8, "bus_position": 5, "bus_arrival": 3}


def test_exclude_trial_option() -> None:
    store = FakeStore()
    code, captured = run(
        ["load", "--date", "2026-10-07", "--data-dir", DATA_DIR, "--exclude-trial"], store
    )
    assert code == cli.EXIT_OK
    assert "시운전 제외 1" in captured.text
    assert store.counts()["raw_poll"] == 7


def test_range_with_missing_day_records_none() -> None:
    store = FakeStore()
    code, captured = run(
        ["load", "--from", "2026-10-06", "--to", "2026-10-07", "--data-dir", DATA_DIR], store
    )
    assert code == cli.EXIT_OK
    assert "수집 파일 없음" in captured.text
    assert "== 합계 2일 ==" in captured.text
    days = store.state.service_days
    assert [days[d].operation_kind for d in sorted(days)] == ["none", "regular"]


def test_load_failure_rolls_back_and_exits_3() -> None:
    store = FakeStore(fail_on="bus_position")
    code, captured = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR], store)
    assert code == cli.EXIT_LOAD_FAILED
    assert store.counts()["raw_poll"] == 0
    assert any("InjectedFailure" in line and "롤백" in line for line in captured.err)
    assert store.closed is True


def test_range_stops_at_failed_day() -> None:
    store = FakeStore(fail_on="raw_poll")
    code, _ = run(
        ["load", "--from", "2026-10-07", "--to", "2026-10-08", "--data-dir", DATA_DIR], store
    )
    assert code == cli.EXIT_LOAD_FAILED
    assert store.state.service_days == {}


def test_connect_failure_exits_3() -> None:
    def factory() -> FakeStore:
        raise OSError("접속 실패 상세(출력하면 안 됨)")

    code, captured = run(
        ["load", "--date", "2026-10-07", "--data-dir", DATA_DIR], store_factory=factory
    )
    assert code == cli.EXIT_LOAD_FAILED
    assert captured.err == ["DB 접속 실패: OSError"]


def test_missing_database_url_exits_2(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DATABASE_URL", raising=False)
    code, captured = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR])
    assert code == cli.EXIT_CONFIG_MISSING
    assert "DATABASE_URL" in captured.err[0]


@pytest.mark.parametrize(
    "argv",
    [
        [],
        ["load"],
        ["load", "--date", "2026-1-07"],
        ["load", "--date", "2026-02-30"],
        ["load", "--from", "2026-10-07"],
        ["load", "--from", "2026-10-08", "--to", "2026-10-07"],
        ["load", "--date", "2026-10-07", "--to", "2026-10-08"],
        ["load", "--from", "2025-01-01", "--to", "2026-10-07"],
        ["load", "--date", "2026-10-07", "--include-trial", "--exclude-trial"],
        ["reference", "--date", "어제"],
    ],
)
def test_usage_errors_exit_1(argv: list[str]) -> None:
    code, _ = run(argv, FakeStore())
    assert code == cli.EXIT_USAGE


def test_missing_data_dir_exits_1(tmp_path: Path) -> None:
    code, captured = run(
        ["load", "--date", "2026-10-07", "--data-dir", str(tmp_path / "없음")], FakeStore()
    )
    assert code == cli.EXIT_USAGE
    assert "데이터 폴더가 없다" in captured.err[0]


def test_reference_dry_run_and_load() -> None:
    code, captured = run(["reference", "--data-dir", DATA_DIR, "--dry-run"])
    assert code == cli.EXIT_OK
    assert "== 기준정보 reference/2026-10-06 ==" in captured.text
    assert "route          18" in captured.text
    assert "station        6" in captured.text
    # dry-run 은 DB 를 보지 않으므로 '남길 순번'을 보여 준다.
    assert "  1306(235000123): 순번 1-3 만 남김(그 밖의 DB 순번을 지움, DB 확인 안 함)" in (
        captured.out
    )
    assert "  G1300(235000092): 순번 1-4 업서트, 건너뛴 항목 1개라 지우지 않음" in captured.out
    assert captured.err == [
        "경고: G1300 정류장 목록에 건너뛴 항목 1개가 있어 기준정보에서 없어진 순번을 지우지 않는다"
    ]

    store = FakeStore()
    code, captured = run(["reference", "--date", "2026-10-06", "--data-dir", DATA_DIR], store)
    assert code == cli.EXIT_OK
    assert len(store.state.route_stations) == 7
    assert "지운 순번      0" in captured.text
    assert "  1306(235000123): 순번 1-3 업서트, 지운 순번 없음" in captured.out


def test_seq_ranges() -> None:
    assert cli.seq_ranges([3, 1, 2, 7, 9, 10]) == "1-3, 7, 9-10"
    assert cli.seq_ranges([]) == "없음"


def test_missing_sslrootcert_file_exits_2(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_url="postgresql://u@h/db",
        database_sslrootcert=str(tmp_path / "없는-ca.crt"),
    )
    code, captured = run(
        ["load", "--date", "2026-10-07", "--data-dir", DATA_DIR], settings=settings
    )
    assert code == cli.EXIT_CONFIG_MISSING
    assert "DATABASE_SSLROOTCERT" in captured.err[0]


def test_missing_target_ids_exit_2(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> None:
        raise ValueError("COLLECT_TARGET.board_station_id 가 없다")

    monkeypatch.setattr(cli.LoadTargets, "from_settings", staticmethod(broken))
    code, captured = run(["load", "--date", "2026-10-07", "--data-dir", DATA_DIR], FakeStore())
    assert code == cli.EXIT_CONFIG_MISSING
    assert captured.err == ["설정 누락: COLLECT_TARGET.board_station_id 가 없다"]


def test_reference_missing_folder_exits_1() -> None:
    code, captured = run(["reference", "--date", "2026-10-01", "--data-dir", DATA_DIR], FakeStore())
    assert code == cli.EXIT_USAGE
    assert "기준정보 폴더가 없다" in captured.err[0]


def test_describe_error_hides_message() -> None:
    class FakeDbError(Exception):
        sqlstate = "23514"
        diag = SimpleNamespace(constraint_name="raw_poll_target_chk")

    text = cli.describe_error(FakeDbError("postgresql://user:secret@host/db"))
    assert text == "FakeDbError sqlstate=23514 constraint=raw_poll_target_chk"
    assert "secret" not in text
