"""report 명령: 작은 JSONL 로 표 형식·trial 포함 여부·CSV·종료 코드를 확인한다.

픽스처 내용(2026-10-06, 목표 = 덕현초교 잠실행 235000392):
- G1300 차량 A: 직전(12) 출발 뒤 잔여석 0 → strict, 라벨 1
- G1300 차량 B: 11 에서 바로 14 → passed_within_poll
- 1306 차량 C: 직전(10) 도착 뒤 교차로 통과(0)만 → relaxed, 라벨 0
- G1300 차량 D(시운전 40초): 직전 출발 잔여석 1 → strict, 라벨 0
"""

import csv
import json
from pathlib import Path

import pytest

from app.labeling import cli
from app.labeling.report import CSV_COLUMNS
from tests.labeling.helpers import (
    BOARD_STATION_ID,
    G1300,
    R1306,
    arrival_line,
    at,
    gbis_item,
    location_line,
    write_jsonl,
)

DATE = "2026-10-06"
VEH_A, VEH_B, VEH_C, VEH_D = "235000101", "235000102", "235000201", "235000109"


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    root = tmp_path / "collected"

    def g(veh: str, seq: int, state: int, seat: int) -> dict:
        station = BOARD_STATION_ID if seq == 13 else None
        return gbis_item(veh, seq, state, seat, route_id=G1300, station_id=station)

    def r(veh: str, seq: int, state: int, seat: int) -> dict:
        station = BOARD_STATION_ID if seq == 11 else None
        return gbis_item(veh, seq, state, seat, route_id=R1306, station_id=station)

    lines = [
        location_line(at(6, 0), [g(VEH_A, 11, 2, 6), g(VEH_B, 10, 0, 4)], route_id=G1300),
        location_line(at(6, 0), [r(VEH_C, 10, 1, 7)], route_id=R1306),
        arrival_line(at(6, 0)),
        location_line(at(6, 1), [g(VEH_A, 12, 2, 2), g(VEH_B, 11, 0, 4)], route_id=G1300),
        location_line(at(6, 1), [r(VEH_C, 10, 0, 5)], route_id=R1306),
        location_line(at(6, 2), [g(VEH_A, 12, 0, 0), g(VEH_B, 14, 0, 3)], route_id=G1300),
        location_line(at(6, 2), [r(VEH_C, 11, 1, 5)], route_id=R1306),
        location_line(at(6, 3), [g(VEH_A, 13, 1, 0), g(VEH_B, 15, 0, 3)], route_id=G1300),
        location_line(
            at(10, 30, 0), [g(VEH_D, 12, 2, 1)], route_id=G1300, mode="trial", interval_sec=40
        ),
        location_line(
            at(10, 30, 40), [g(VEH_D, 13, 1, 1)], route_id=G1300, mode="trial", interval_sec=40
        ),
    ]
    write_jsonl(root / DATE / "raw_poll.jsonl", lines)
    return root


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, list[str]]:
    code = cli.main(["report", "--date", DATE, *args])
    return code, capsys.readouterr().out.splitlines()


def _section(lines: list[str], title: str) -> list[list[str]]:
    """제목 줄 다음의 머리글을 건너뛰고 빈 줄 전까지의 행을 공백으로 나눠 돌려준다."""
    start = next(i for i, line in enumerate(lines) if line.startswith(title))
    rows: list[list[str]] = []
    for line in lines[start + 2 :]:
        if not line.strip():
            break
        rows.append(line.split())
    return rows


def test_report_default_excludes_trial(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = _run(capsys, "--data-dir", str(data_dir))
    assert code == cli.EXIT_OK
    assert lines[0] == f"[운행편 라벨 리포트] date={DATE} include_trial=false"
    assert any("trial 제외 2" in line for line in lines)

    # route trips strict relaxed unconfirmed zero_strict zero_relaxed
    assert _section(lines, "[노선별 등급]") == [
        ["G1300", "2", "1", "0", "1", "1", "0"],
        ["1306", "1", "0", "1", "0", "0", "0"],
        ["ALL", "3", "1", "1", "1", "1", "0"],
    ]
    reasons = {(row[0], row[1]): row[2:] for row in _section(lines, "[미확인 사유]")}
    assert reasons[("G1300", "passed_within_poll")] == ["1", "50.0%", "50.0%"]
    assert reasons[("G1300", "never_reached_target")] == ["0", "0.0%", "-"]
    assert reasons[("1306", "seat_unknown")] == ["0", "0.0%", "0.0%"]
    assert reasons[("ALL", "passed_within_poll")] == ["1", "33.3%", "33.3%"]
    assert _section(lines, "[플래그]") == [
        ["G1300", "2", "0", "0"],
        ["1306", "1", "0", "0"],
        ["ALL", "3", "0", "0"],
    ]
    # 위치 기록의 stationId 로 목표 순번을 확인했다.
    assert any(
        "G1300 routeId=235000092 순번 13" in line and "일치 13(1건)" in line for line in lines
    )
    assert not any("경고" in line for line in lines)


def test_report_include_trial(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = _run(capsys, "--data-dir", str(data_dir), "--include-trial")
    assert code == cli.EXIT_OK
    assert lines[0].endswith("include_trial=true")
    assert _section(lines, "[노선별 등급]")[0] == ["G1300", "3", "2", "0", "1", "1", "0"]
    assert _section(lines, "[플래그]")[0] == ["G1300", "3", "1", "0"]
    assert any("interval_sec 40,60" in line for line in lines)


def test_report_writes_csv(
    data_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    csv_path = tmp_path / "out" / "labels.csv"
    code, lines = _run(capsys, "--data-dir", str(data_dir), "--csv", str(csv_path))
    assert code == cli.EXIT_OK
    assert lines[-1] == f"CSV 저장: {csv_path} (3행)"

    with csv_path.open(encoding="utf-8-sig", newline="") as fp:
        rows = list(csv.DictReader(fp))
    assert tuple(rows[0].keys()) == CSV_COLUMNS
    by_veh = {row["veh_id"]: row for row in rows}
    assert by_veh[VEH_A]["grade"] == "strict"
    assert by_veh[VEH_A]["label"] == "1"
    assert by_veh[VEH_A]["last_seat"] == "0"
    assert by_veh[VEH_A]["target_arrival_at"] == "2026-10-06T06:03:00+09:00"
    assert by_veh[VEH_A]["route_name"] == "G1300"
    assert by_veh[VEH_B]["grade"] == "unconfirmed"
    assert by_veh[VEH_B]["label"] == ""
    assert by_veh[VEH_B]["reason"] == "passed_within_poll"
    assert by_veh[VEH_C]["grade"] == "relaxed"
    assert by_veh[VEH_C]["interval_sec"] == "60"


def test_report_missing_file_returns_2(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, lines = _run(capsys, "--data-dir", str(tmp_path / "empty"))
    assert code == cli.EXIT_INPUT_MISSING
    assert lines[0].startswith("수집 파일이 없다:")


def test_report_rejects_bad_date() -> None:
    with pytest.raises(SystemExit) as excinfo:
        cli.main(["report", "--date", "2026-13-01"])
    assert excinfo.value.code == 2


def test_report_warns_when_observed_seq_differs(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "collected"
    item = gbis_item(VEH_A, 14, 1, 3, route_id=G1300, station_id=BOARD_STATION_ID)
    write_jsonl(root / DATE / "raw_poll.jsonl", [location_line(at(6, 0), [item], route_id=G1300)])
    code, lines = _run(capsys, "--data-dir", str(root))
    assert code == cli.EXIT_TARGET_MISMATCH
    assert any("불일치 14(1건)" in line for line in lines)
    assert any(line.strip().startswith("경고") for line in lines)


def test_report_checks_targets_json(data_dir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    reference = data_dir / "reference" / DATE / "targets.json"
    reference.parent.mkdir(parents=True)
    reference.write_text(
        json.dumps(
            {
                "routes": [
                    {"route_id": G1300, "board": {"stationId": BOARD_STATION_ID, "stationSeq": 13}},
                    {"route_id": R1306, "board": {"stationId": BOARD_STATION_ID, "stationSeq": 12}},
                ]
            }
        ),
        encoding="utf-8",
    )
    code, lines = _run(capsys, "--data-dir", str(data_dir))
    assert code == cli.EXIT_TARGET_MISMATCH
    g1300_line = next(line for line in lines if "G1300 routeId=" in line)
    r1306_line = next(line for line in lines if "1306 routeId=" in line and "G1300" not in line)
    assert g1300_line.endswith("targets.json: 일치")
    assert r1306_line.endswith("targets.json: 불일치 12")
