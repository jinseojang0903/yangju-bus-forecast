"""backtest 명령: 합성 JSONL 로 표·건너뛴 날짜·CSV·종료 코드를 확인한다.

장면과 기대값은 tests/evaluation/helpers.py 설명과 같다. 2026-10-23(금)은 파일을 두지 않는다.
"""

import csv
from datetime import date, time
from pathlib import Path

import pytest

from app.core.settings import REPO_ROOT
from app.evaluation import cli
from app.evaluation.report import CSV_COLUMNS, display_path
from tests.evaluation.helpers import (
    BASE_DAYS,
    TEST_DAY,
    build_scenario_records,
    trip_records,
    write_day_file,
)
from tests.labeling.helpers import pos

DATE_FROM, DATE_TO = "2026-10-07", "2026-10-23"


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("collected")
    for day, records in build_scenario_records().items():
        write_day_file(root, day, records)
    return root


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, list[str]]:
    code = cli.main(["backtest", *args])
    return code, capsys.readouterr().out.splitlines()


def _cells(line: str) -> list[str]:
    return [cell.strip() for cell in line.strip().strip("|").split("|")]


def _table_rows(lines: list[str], heading: str) -> dict[tuple[str, str], list[str]]:
    """heading 줄 뒤 첫 표의 행을 (첫 칸, 둘째 칸) → 나머지 칸으로 돌려준다."""
    start = next(i for i, line in enumerate(lines) if line.startswith(heading))
    table_start = next(i for i in range(start + 1, len(lines)) if lines[i].startswith("|"))
    rows: dict[tuple[str, str], list[str]] = {}
    for line in lines[table_start + 2 :]:
        if not line.startswith("|"):
            break
        first, second, *rest = _cells(line)
        rows[(first, second)] = rest
    return rows


def test_backtest_prints_tables(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = _run(capsys, "--from", DATE_FROM, "--to", DATE_TO, "--data-dir", str(data_dir))

    assert code == cli.EXIT_OK
    assert lines[0].startswith("# 백테스트")
    assert "- 요청 기간: 2026-10-07 ~ 2026-10-23" in lines
    assert "- 관측 기간(실제 읽은 첫날 ~ 마지막 날): 2026-10-07 ~ 2026-10-22" in lines
    assert "- 읽은 평일 수: 12일 (그중 공휴일 1일: 2026-10-09)" in lines
    assert "- 건너뛴 날짜(평일인데 수집 파일 없음) 1일: 2026-10-23" in lines
    assert any(line.startswith("- 주말 4일은 수집 요일이 아니라 읽지 않음") for line in lines)
    assert "- 정답 등급: strict+relaxed" in lines
    assert any("시운전 22건" in line for line in lines)  # X 한 대의 기록 수
    assert not any("경고" in line for line in lines)
    assert f"- 데이터 폴더: {data_dir.name}" in lines  # 저장소 밖이면 이름만
    assert not any(str(data_dir.parent) in line for line in lines)
    assert any(line.startswith("- 한계: 기준 시각은 실제 도착 − L분이다.") for line in lines)

    # 노선 | 운행편 시운전 공휴일·주말 도착없음 시간대밖 미확인 등급 대상 0석 strict relaxed
    sample = {key[0]: [key[1], *rest] for key, rest in _table_rows(lines, "## 1. 표본").items()}
    assert sample["G1300"] == ["76", "1", "6", "0", "11", "0", "0", "58", "23", "58", "0"]
    assert sample["1306"] == ["1", "0", "0", "0", "0", "0", "0", "1", "1", "1", "0"]
    assert sample["ALL"] == ["77", "1", "6", "0", "11", "0", "0", "59", "24", "59", "0"]

    # 대상 제공 보류(부족) 보류(누락) TP FN FP TN 정밀도 재현율 Brier
    case_rows = _table_rows(lines, "## 2. 방법 B")
    assert case_rows[("5분", "G1300")] == [
        *["58", "15", "42", "1", "1", "1", "1", "12"],
        *["50.0%", "50.0%", f"{(2 + 2 / 900) / 15:.4f}"],
    ]
    assert case_rows[("15분", "G1300")][:8] == ["58", "14", "43", "1", "1", "1", "1", "11"]
    assert case_rows[("5분", "1306")] == ["1", "0", "0", "1", "0", "0", "0", "0", "-", "-", "-"]
    assert case_rows[("5분", "ALL")][:4] == ["59", "15", "42", "2"]

    # 대상 판단 판단불가 TP FN FP TN 정밀도 재현율 Brier
    simple_rows = _table_rows(lines, "### (a)")
    assert simple_rows[("10분", "G1300")] == [
        *["58", "57", "1", "1", "22", "1", "33"],
        *["50.0%", "4.3%", f"{23 / 57:.4f}"],
    ]
    # 운행편 TP FN FP TN 정밀도 재현율 Brier
    same_trips_rows = _table_rows(lines, "### (b)")
    assert same_trips_rows[("5분", "G1300")] == [
        *["15", "0", "2", "0", "13"],
        *["-", "0.0%", f"{2 / 15:.4f}"],
    ]

    reference_rows = _table_rows(lines, "## 4. 참고")
    assert reference_rows[("5분", "G1300")][:3] == ["58", "23", "39.7%"]
    assert any("기획안 정의 확인 필요" in line for line in lines)
    assert not any("어림셈" in line for line in lines)


def test_backtest_writes_csv(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "out" / "rows.csv"
    code, lines = _run(
        capsys,
        *("--from", DATE_FROM, "--to", DATE_TO),
        *("--data-dir", str(data_dir), "--out", str(out_path)),
    )

    assert code == cli.EXIT_OK
    # 저장소 밖 경로는 이름만 출력한다(로컬 사용자 경로를 드러내지 않는다).
    assert lines[-1] == f"CSV 저장: rows.csv ({59 * 3}행)"
    assert not any(str(tmp_path) in line for line in lines)
    with out_path.open(encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    assert len(rows) == 59 * 3

    def row(arrival: str, lead: int, route_name: str = "G1300") -> dict[str, str]:
        return next(
            r
            for r in rows
            if r["target_arrival_at"] == f"{TEST_DAY.isoformat()}T{arrival}:00+09:00"
            and r["lead_time_min"] == str(lead)
            and r["route_name"] == route_name
        )

    false_alarm = row("06:20", 5)  # T2: 방법 B 가 잘못 알린 만차
    assert false_alarm["reference_at"] == "2026-10-22T06:15:00+09:00"
    assert (false_alarm["seats"], false_alarm["headway_min"]) == ("5", "15.00")
    assert (false_alarm["status"], false_alarm["n"], false_alarm["k"]) == ("ok", "21", "21")
    assert false_alarm["no_seat_probability"] == "1.0"
    assert (false_alarm["simple_alert"], false_alarm["case_alert"]) == ("0", "1")
    assert (false_alarm["label"], false_alarm["label_grade"]) == ("0", "strict")
    assert len(false_alarm["case_trip_keys"].split(";")) == 21
    assert false_alarm["trip_key"].startswith("2026-10-22_235000092_")

    unknown_seat = row("07:35", 5)  # T7: 잔여석 −1 은 빈칸이지 0 이 아니다
    assert (unknown_seat["seats"], unknown_seat["status"]) == ("", "missing_input")
    assert unknown_seat["uncomputable_reason"] == "seat_unknown"
    assert (unknown_seat["simple_alert"], unknown_seat["case_alert"]) == ("", "")
    assert unknown_seat["no_seat_probability"] == ""

    no_preceding = row("06:30", 5, "1306")
    assert (no_preceding["seats"], no_preceding["headway_min"]) == ("0", "")
    assert no_preceding["uncomputable_reason"] == "no_preceding_vehicle"
    assert (no_preceding["simple_alert"], no_preceding["case_alert"]) == ("1", "")


def test_backtest_strict_only_header(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = _run(
        capsys, "--from", DATE_FROM, "--to", DATE_TO, "--data-dir", str(data_dir), "--strict-only"
    )

    assert code == cli.EXIT_OK
    assert "- 정답 등급: strict" in lines


def test_backtest_without_any_file_returns_2(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines = _run(
        capsys, "--from", DATE_FROM, "--to", DATE_TO, "--data-dir", str(tmp_path / "empty")
    )

    assert code == cli.EXIT_INPUT_MISSING
    assert lines[0].startswith("기간 안에 읽을 수집 파일이 없다: 2026-10-07 ~ 2026-10-23")
    assert lines[1].startswith("건너뛴 날짜(평일인데 수집 파일 없음) 13일: 2026-10-07, 2026-10-08,")
    assert lines[1].endswith("2026-10-23")
    assert "2026-10-10" not in lines[1]  # 주말은 건너뛴 날짜로 세지 않는다


def test_backtest_reversed_period_returns_2(
    data_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code, lines = _run(capsys, "--from", DATE_TO, "--to", DATE_FROM, "--data-dir", str(data_dir))

    assert code == cli.EXIT_INPUT_MISSING
    assert lines[0].startswith("기간이 잘못됐다")


def test_backtest_rejects_bad_date() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["backtest", "--from", "2026-13-01", "--to", DATE_TO])
    assert excinfo.value.code == 2


def test_backtest_weekend_file_is_reported_but_not_read(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "collected"
    saturday, monday = date(2026, 10, 10), date(2026, 10, 12)
    for day in (saturday, monday):
        write_day_file(root, day, trip_records(day, "1", time(6, 30), seats=5, last_seat=0))
    code, lines = _run(
        capsys, "--from", "2026-10-10", "--to", "2026-10-12", "--data-dir", str(root)
    )

    assert code == cli.EXIT_OK
    assert "- 읽은 평일 수: 1일 (그중 공휴일 0일: 없음)" in lines
    assert "- 주말 2일은 수집 요일이 아니라 읽지 않음 (파일이 있었던 날: 2026-10-10)" in lines
    # 운행편 1(월요일 것만), 평가 대상 1
    assert _table_rows(lines, "## 1. 표본")[("G1300", "1")][6] == "1"


def test_backtest_warns_when_target_seq_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    # 목표 stationId 가 순번 14 로 관측되면 설정의 순번(13)과 다르다.
    root = tmp_path / "collected"
    day = BASE_DAYS[0]
    records = trip_records(day, "1", time(6, 30), seats=5, last_seat=0)
    moved = [
        pos(
            r.collected_at,
            r.station_seq,
            r.state_cd,
            r.remain_seat_cnt,
            veh_id=r.veh_id,
            station_id="235000392" if r.station_seq == 14 else "277000001",
        )
        for r in records
    ]
    write_day_file(root, day, moved)
    code, lines = _run(
        capsys, "--from", day.isoformat(), "--to", day.isoformat(), "--data-dir", str(root)
    )

    assert code == cli.EXIT_TARGET_MISMATCH
    assert any(line.startswith("- 경고: 목표 순번") and day.isoformat() in line for line in lines)


def test_backtest_counts_broken_lines(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "collected"
    day = BASE_DAYS[0]
    path = write_day_file(root, day, trip_records(day, "1", time(6, 30), seats=5, last_seat=0))
    with path.open("a", encoding="utf-8") as fp:
        fp.write('{"collected_at": "2026-10-07T06:40:00+09:00", "api": "getBusLoc\n')
    code, lines = _run(
        capsys, "--from", day.isoformat(), "--to", day.isoformat(), "--data-dir", str(root)
    )

    assert code == cli.EXIT_OK
    assert any("깨진 줄 1)" in line for line in lines)


# --- 출력 파일·파일 오류 ----------------------------------------------------------
def _run_one_day(
    capsys: pytest.CaptureFixture[str], root: Path, *args: str
) -> tuple[int, list[str]]:
    day = BASE_DAYS[0].isoformat()
    return _run(capsys, "--from", day, "--to", day, "--data-dir", str(root), *args)


@pytest.fixture
def one_day_dir(tmp_path: Path) -> Path:
    root = tmp_path / "collected"
    day = BASE_DAYS[0]
    write_day_file(root, day, trip_records(day, "1", time(6, 30), seats=5, last_seat=0))
    return root


def test_existing_out_file_is_not_overwritten_without_force(
    one_day_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "rows.csv"
    out_path.write_text("keep", encoding="utf-8")

    code, lines = _run_one_day(capsys, one_day_dir, "--out", str(out_path))

    assert code == cli.EXIT_INPUT_MISSING
    assert lines == ["출력 파일이 이미 있다: rows.csv (덮어쓰려면 --force)"]
    assert out_path.read_text(encoding="utf-8") == "keep"


def test_force_overwrites_out_file(
    one_day_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out_path = tmp_path / "rows.csv"
    out_path.write_text("old", encoding="utf-8")

    code, lines = _run_one_day(capsys, one_day_dir, "--out", str(out_path), "--force")

    assert code == cli.EXIT_OK
    assert lines[-1] == "CSV 저장: rows.csv (3행)"
    assert out_path.read_text(encoding="utf-8-sig").startswith("trip_key,")


def test_write_error_is_one_line_with_file_name_only(
    one_day_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocker = tmp_path / "not_a_dir"
    blocker.write_text("", encoding="utf-8")  # 폴더 자리에 파일이 있어 CSV 를 만들 수 없다

    code, lines = _run_one_day(capsys, one_day_dir, "--out", str(blocker / "rows.csv"))

    assert code == cli.EXIT_INPUT_MISSING
    assert lines[-1].startswith("파일을 읽거나 쓰지 못했다: not_a_dir (")
    assert not any(str(tmp_path) in line for line in lines)
    assert not any("Traceback" in line for line in lines)


def test_non_utf8_input_is_one_line_error(
    one_day_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = one_day_dir / BASE_DAYS[0].isoformat() / "raw_poll.jsonl"
    with path.open("ab") as fp:
        fp.write(b'{"api": "\xff\xfe"}\n')

    code, lines = _run_one_day(capsys, one_day_dir)

    assert code == cli.EXIT_INPUT_MISSING
    assert lines == ["수집 파일을 UTF-8 로 읽지 못했다(UnicodeDecodeError)"]


def test_display_path_inside_and_outside_repo(tmp_path: Path) -> None:
    inside = REPO_ROOT / "data" / "backtest" / "rows.csv"

    assert display_path(inside) == "data/backtest/rows.csv"
    assert display_path(REPO_ROOT) == "."
    assert display_path(tmp_path / "somewhere" / "rows.csv") == "rows.csv"
