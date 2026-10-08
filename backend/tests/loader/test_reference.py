"""기준정보 폴더 → route·station·route_station 행.

픽스처 폴더: tests/fixtures/loader/reference/2026-10-06.

픽스처 기록: 노선 검색 G1300(G1300·G1300N·settings 에 없는 노선), 1306(단일 객체), G1300 정류장
4개 + 이름 없는 항목, 1306 정류장 3개(덕현초교는 G1300 과 공유), settings 에 없는 노선의 정류장
목록, ok=false 기록.
"""

import json
from datetime import date
from pathlib import Path

import pytest

from app.core.settings import YANGJU_ROUTES
from app.loader.reference import _coordinate, find_reference_dir, read_reference
from tests.loader.fakes import FIXTURE_DATA_DIR, REFERENCE_DAY

FOLDER = FIXTURE_DATA_DIR / "reference" / "2026-10-06"


def test_read_reference_counts() -> None:
    batch = read_reference(FOLDER, REFERENCE_DAY)
    stats = batch.stats
    assert stats.record_files == 6
    assert stats.route_list_records == 2
    assert stats.route_station_records == 3
    assert stats.unusable_records == 1
    assert stats.unknown_route_records == 1
    assert stats.skipped_station_items == 1
    assert stats.routes_with_details == ["G1300", "1306", "G1300N"]


def test_routes_follow_settings() -> None:
    batch = read_reference(FOLDER, REFERENCE_DAY)
    routes = {r.route_id: r for r in batch.routes}
    assert len(routes) == len(YANGJU_ROUTES) == 18
    assert "999000001" not in routes

    g1300 = routes["235000092"]
    assert (g1300.route_name, g1300.is_collected, g1300.is_label_target) == ("G1300", True, True)
    assert (g1300.collect_interval_sec, g1300.route_type_cd, g1300.admin_name) == (
        10,
        "11",
        "경기도 양주시",
    )
    assert (g1300.start_station_name, g1300.end_station_name) == ("덕정차고지", "잠실광역환승센터")
    assert g1300.source_date == REFERENCE_DAY

    r1306 = routes["235000123"]  # 단일 객체 응답, routeName 이 숫자
    assert (r1306.route_name, r1306.collect_interval_sec, r1306.end_station_name) == (
        "1306",
        30,
        "잠실역",
    )

    night = routes["235000116"]
    assert (night.route_name, night.is_night, night.is_collected) == ("G1300N", True, False)
    assert night.collect_interval_sec is None

    reserved = routes["233000371"]  # 검색 기록이 없는 노선: settings 값만, source_date 없음
    assert (reserved.is_reserved, reserved.collect_interval_sec) == (True, 40)
    assert reserved.route_type_cd is None and reserved.source_date is None
    assert routes["235000085"].note is not None
    assert routes["235000115"].note is None


def test_stations_and_route_stations() -> None:
    batch = read_reference(FOLDER, REFERENCE_DAY)
    stations = {s.station_id: s for s in batch.stations}
    assert len(stations) == 6  # 덕현초교는 두 노선이 공유
    board = stations["235000392"]
    assert (board.station_name, board.mobile_no, board.region_name) == (
        "덕현초교.덕고개",
        "39624",
        "양주",
    )
    # 두 노선 목록을 합친다: 1306 목록은 mobileNo 가 없고 x 가 범위 밖(999.5 → NULL)이라
    # G1300 목록의 값(문자열 좌표 "127.07" 포함)이 남는다.
    assert (board.x, board.y) == (127.07, 37.81)
    assert stations["277102386"].mobile_no is None
    assert batch.stats.skipped_items_by_route == {"235000092": 1}
    assert batch.can_trim("235000123") and not batch.can_trim("235000092")

    seqs = {(r.route_id, r.station_seq): r for r in batch.route_stations}
    assert len(seqs) == 7
    assert seqs[("235000092", 3)].station_id == "235000392"
    assert seqs[("235000092", 4)].is_turn_point is True
    assert seqs[("235000092", 1)].is_turn_point is False
    assert seqs[("235000123", 2)].station_id == "235000392"
    assert ("235000092", 5) not in seqs  # 이름 없는 항목


@pytest.mark.parametrize(
    ("x", "expected"),
    [(127.07, 127.07), ("127.07", 127.07), (181, None), ("nan", None), ("inf", None)]
    + [(10**400, None), (True, None), ("", None), (None, None)],
)
def test_coordinate_range(x: object, expected: float | None) -> None:
    assert _coordinate(x, (-180.0, 180.0)) == expected


def test_broken_and_deep_records_are_unusable(tmp_path: Path) -> None:
    folder = tmp_path / "reference" / "2026-10-06"
    folder.mkdir(parents=True)
    (folder / "01_a.record.json").write_text("{", encoding="utf-8")
    (folder / "02_b.record.json").write_text("[" * 100_000 + "]" * 100_000, encoding="utf-8")
    station_list = {
        "api": "getBusRouteStationListv2",
        "ok": True,
        "params": {"routeId": "235000092"},
        "body": {
            "response": {
                "msgBody": {
                    "busRouteStationList": [
                        {"stationId": 1, "stationSeq": "9" * 5000, "stationName": "긴 숫자"},
                        {"stationId": 2, "stationSeq": 1, "stationName": "정상"},
                    ]
                }
            }
        },
    }
    (folder / "03_c.record.json").write_text(json.dumps(station_list), encoding="utf-8")
    batch = read_reference(folder, REFERENCE_DAY)
    assert batch.stats.unusable_records == 2
    assert batch.stats.skipped_station_items == 1
    assert [r.station_seq for r in batch.route_stations] == [1]


def test_find_reference_dir(tmp_path: Path) -> None:
    root = tmp_path / "reference"
    for name in ("2026-10-01", "2026-10-06", "notes", "2026-13-01"):
        (root / name).mkdir(parents=True)
    assert find_reference_dir(tmp_path, None) == root / "2026-10-06"
    assert find_reference_dir(tmp_path, date(2026, 10, 1)) == root / "2026-10-01"
    assert find_reference_dir(tmp_path, date(2026, 10, 2)) is None
    assert find_reference_dir(tmp_path / "없음", None) is None
