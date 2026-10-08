"""JSONL 해석과 행 변환: 픽스처(tests/fixtures/loader/2026-10-07/raw_poll.jsonl, 키 없음, 합성).

픽스처 줄(물리적 줄 번호):
 1 G1300 위치 ok, 2대(두 번째는 vehId 빈 값·잔여석 -1·stationSeq 문자열)
 2 1306 위치 ok, 목록이 dict 1건(단일 객체 응답)
 3 도착 ok: 77-1(대상 밖), G1300(1·2순위), 1306(1순위만, 2순위는 잔여석·혼잡 0 이지만 차 없음)
 4 1100 위치(대상 밖 노선)
 5 빈 줄
 6 깨진 줄(JSON 이 끊김)
 7 G1300 위치 결과 없음(코드 4)
 8 G1300 위치 ok=false(타임아웃, HTTP 없음)
 9 G1300 위치 시운전(trial, 40초)
10 G1300 위치 mode 가 CHECK 밖(bogus)
11 도착 빈 응답(comMsgHeader 만)
12 G1300 위치 once, 결과 코드 "00", interval_sec 없음
13 JSON 배열(객체 아님)
14 collected_at 에 시간대 없음
"""

from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.loader.jsonl import read_day
from app.loader.rows import (
    JSONL_FILE_PATTERN,
    LoadTargets,
    build_line,
    build_service_day,
    db_result_code,
    id_text,
    indexed_items,
    int2,
    int4,
    jsonl_file_name,
    operation_kind,
    text,
)
from tests.loader.fakes import FIXTURE_DATA_DIR, FIXTURE_DAY, PARTIAL_LINE, copy_fixture

G1300 = "235000092"
R1306 = "235000123"
BOARD = "235000392"


@pytest.fixture(scope="module")
def targets() -> LoadTargets:
    return LoadTargets.from_settings()


@pytest.fixture(scope="module")
def batch(targets: LoadTargets):
    return read_day(FIXTURE_DATA_DIR, FIXTURE_DAY, include_trial=True, targets=targets)


def test_targets_from_settings(targets: LoadTargets) -> None:
    assert dict(targets.location_routes) == {G1300: "G1300", R1306: "1306"}
    assert targets.arrival_station_id == BOARD


def test_read_day_counts_every_line(batch) -> None:
    stats = batch.stats
    assert stats.lines_read == 14
    assert stats.blank_lines == 1
    assert stats.broken_lines == 3  # 끊긴 JSON, 배열, 시간대 없는 collected_at
    assert stats.partial_last_lines == 0
    assert stats.other_target_lines == 1
    assert stats.invalid_mode_lines == 1
    assert stats.trial_lines_excluded == 0
    assert dict(stats.target_lines) == {"G1300": 5, "1306": 1, "도착": 2}
    assert stats.failed_target_lines == 1
    assert stats.position_rows == 5
    assert stats.arrival_rows == 3
    assert stats.arrival_empty_ranks == 1
    assert stats.arrival_other_route_items == 1
    # 모든 줄이 어느 한 갈래로 셈에 들어간다.
    assert stats.target_line_total + stats.skipped_lines == stats.lines_read
    assert [line.raw_poll.line_no for line in batch.lines] == [1, 2, 3, 7, 8, 9, 11, 12]


def test_raw_poll_rows_match_schema_checks(batch) -> None:
    assert batch.jsonl_file == "2026-10-07/raw_poll.jsonl"
    assert JSONL_FILE_PATTERN.fullmatch(batch.jsonl_file)
    by_line = {line.raw_poll.line_no: line.raw_poll for line in batch.lines}

    first = by_line[1]
    assert first.api == "getBusLocationListv2"
    assert (first.route_id, first.station_id) == (G1300, None)
    assert first.ok is True and first.http_status == 200 and first.result_code == "0"
    assert first.mode == "run" and first.interval_sec == 10 and first.is_holiday is False
    assert first.collected_at.utcoffset().total_seconds() == 9 * 3600

    arrival = by_line[3]
    assert (arrival.api, arrival.route_id, arrival.station_id) == (
        "getBusArrivalListv2",
        None,
        BOARD,
    )

    no_result = by_line[7]
    assert no_result.ok is True and no_result.result_code == "4"

    failed = by_line[8]
    assert failed.ok is False and failed.http_status is None and failed.result_code is None

    once = by_line[12]
    assert once.mode == "once" and once.interval_sec is None and once.result_code == "0"

    assert by_line[9].mode == "trial"
    assert by_line[11].result_code is None  # 빈 응답


def test_bus_position_rows(batch) -> None:
    by_line = {line.raw_poll.line_no: line for line in batch.lines}
    first, second = by_line[1].positions
    assert (first.item_index, first.veh_id, first.station_seq, first.state_cd) == (
        0,
        "235000101",
        12,
        2,
    )
    assert (first.remain_seat_cnt, first.crowded, first.plate_no) == (3, 2, "테스트01")
    assert (first.interval_sec, first.mode) == (10, "run")
    # 원값 그대로: 잔여석 -1 을 남기고, vehId 가 비어도 행은 남긴다(운행편 재구성에서 뺀다).
    assert second.item_index == 1
    assert second.remain_seat_cnt == -1
    assert second.veh_id is None and second.plate_no is None
    assert second.station_seq == 5  # 문자열 "5"

    (single,) = by_line[2].positions  # 단일 객체 응답
    assert (single.item_index, single.route_id, single.veh_id) == (0, R1306, "235010109")

    assert by_line[7].positions == ()  # 결과 없음
    assert by_line[8].positions == ()  # ok=false


def test_bus_arrival_rows(batch) -> None:
    by_line = {line.raw_poll.line_no: line for line in batch.lines}
    arrivals = by_line[3].arrivals
    keys = [(a.route_id, a.arrival_rank, a.item_index) for a in arrivals]
    assert keys == [(G1300, 1, 1), (G1300, 2, 1), (R1306, 1, 2)]
    g1, g2, r1 = arrivals
    assert (g1.veh_id, g1.predict_time_sec, g1.predict_time_min, g1.remain_seat_cnt) == (
        "235000101",
        93,
        1,
        0,
    )
    assert (g1.location_no, g1.sta_order, g1.crowded, g1.flag, g1.station_id) == (
        1,
        13,
        3,
        "PASS",
        BOARD,
    )
    assert (g2.veh_id, g2.predict_time_sec, g2.remain_seat_cnt) == ("235000102", 401, 25)
    assert (r1.veh_id, r1.predict_time_sec, r1.plate_no) == ("235010109", 1372, "테스트02")
    assert by_line[3].arrival_empty_ranks == 1
    assert by_line[3].arrival_other_route_items == 1
    assert by_line[11].arrivals == ()  # 빈 응답


def test_exclude_trial_counts_skipped_lines(targets: LoadTargets) -> None:
    batch = read_day(FIXTURE_DATA_DIR, FIXTURE_DAY, include_trial=False, targets=targets)
    assert batch.stats.trial_lines_excluded == 1
    assert batch.stats.target_lines["G1300"] == 4
    assert batch.stats.position_rows == 4
    assert 9 not in [line.raw_poll.line_no for line in batch.lines]
    # 운영 구분은 넣지 않은 줄까지 포함한 그날 파일 기준이다.
    assert batch.service_day.operation_kind == "regular"


def test_partial_last_line_is_skipped_until_complete(tmp_path: Path, targets: LoadTargets) -> None:
    data_dir, path = copy_fixture(tmp_path)
    with path.open("ab") as fp:
        fp.write(PARTIAL_LINE.encode("utf-8"))  # 줄바꿈 없음 = 쓰는 중

    batch = read_day(data_dir, FIXTURE_DAY, include_trial=True, targets=targets)
    assert batch.stats.lines_read == 15
    assert batch.stats.partial_last_lines == 1
    assert batch.stats.broken_lines == 3
    assert len(batch.lines) == 8

    with path.open("ab") as fp:
        fp.write(b"\n")
    batch = read_day(data_dir, FIXTURE_DAY, include_trial=True, targets=targets)
    assert batch.stats.partial_last_lines == 0
    assert batch.lines[-1].raw_poll.line_no == 15
    assert batch.lines[-1].positions[0].station_seq == 14


def test_missing_file_gives_empty_batch(tmp_path: Path, targets: LoadTargets) -> None:
    batch = read_day(tmp_path, date(2026, 10, 10), include_trial=True, targets=targets)
    assert batch.stats.file_exists is False
    assert batch.lines == []
    assert batch.service_day.operation_kind == "none"
    assert batch.service_day.is_weekday is False  # 토요일


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0", "0"),
        ("00", "0"),
        (4, "4"),
        ("22", "22"),
        ("GATEWAY_ERROR", "GATEWAY_ERROR"),
        ("x" * 64, "x" * 64),
        ("x" * 65, None),
        ("bad code!", None),
        ("", None),
        (None, None),
        (True, None),
        ("²", None),
    ],
)
def test_db_result_code(value: object, expected: str | None) -> None:
    assert db_result_code(value) == expected


def test_indexed_items_keep_original_positions() -> None:
    assert indexed_items({"a": 1}) == [(0, {"a": 1})]
    assert indexed_items([{"a": 1}, "x", {"b": 2}]) == [(0, {"a": 1}), (2, {"b": 2})]
    assert indexed_items(None) == []
    assert indexed_items("") == []


def test_int2_range() -> None:
    assert int2("3") == 3
    assert int2(40000) is None
    assert int2("") is None


def test_jsonl_file_name_rejects_bad_names() -> None:
    assert jsonl_file_name(FIXTURE_DAY, "raw_poll.jsonl") == "2026-10-07/raw_poll.jsonl"
    with pytest.raises(ValueError):
        jsonl_file_name(FIXTURE_DAY, "../raw_poll.jsonl")


@pytest.mark.parametrize(
    ("modes", "expected"),
    [
        ({"run": 3}, "regular"),
        ({"run": 3, "once": 1, "trial": 2}, "regular"),
        ({"once": 1, "trial": 2}, "trial"),  # 정식 수집 없이 시험 호출만 한 날
        ({"once": 4}, "trial"),
        ({"trial": 2}, "trial"),
        ({}, "none"),
    ],
)
def test_operation_kind(modes: dict[str, int], expected: str) -> None:
    assert operation_kind(modes) == expected


def test_service_day_weekday_and_holiday() -> None:
    wednesday = build_service_day(date(2026, 10, 7), {"run": 1})
    assert (wednesday.is_weekday, wednesday.is_holiday, wednesday.operation_kind) == (
        True,
        False,
        "regular",
    )
    hangul_day = build_service_day(date(2026, 10, 9), {"run": 1})  # 금요일 공휴일
    assert (hangul_day.is_weekday, hangul_day.is_holiday) == (True, True)
    saturday = build_service_day(date(2026, 10, 10), {})
    assert (saturday.is_weekday, saturday.operation_kind) == (False, "none")


def test_service_holiday_uses_settings_list(monkeypatch: pytest.MonkeyPatch) -> None:
    import app.loader.rows as rows_module

    extra = date(2026, 10, 14)  # 라이브러리상 평일(수요일)
    assert rows_module.is_service_holiday(extra) is False
    monkeypatch.setattr(
        rows_module, "SERVICE_HOURS", SimpleNamespace(holidays=(date(2026, 10, 9), extra))
    )
    assert rows_module.is_service_holiday(extra) is True


def test_raw_poll_keeps_collected_holiday_flag(targets: LoadTargets) -> None:
    # raw_poll.is_holiday 는 수집 당시 JSONL 플래그 그대로다(날짜 분류는 service_day 가 기준).
    line = {
        "collected_at": "2026-10-09T06:00:00+09:00",
        "api": "getBusLocationListv2",
        "params": {"routeId": G1300},
        "ok": True,
        "is_holiday": False,
        "body": None,
    }
    parsed = build_line(line, 1, "run", "G1300", targets)
    assert parsed.raw_poll.is_holiday is False
    del line["is_holiday"]  # 이전 형식 줄: 수집기와 같은 라이브러리로 판정
    assert build_line(line, 1, "run", "G1300", targets).raw_poll.is_holiday is True


def test_values_that_postgres_rejects_become_null() -> None:
    assert text("a\x00b") is None
    assert text("\ud800") is None  # 짝 없는 서로게이트
    assert id_text("\udfff12") is None
    assert text(" 정상 ") == "정상"
    assert int4("9" * 5000) is None  # int 자릿수 상한을 넘는 숫자 문자열


def test_deeply_nested_line_is_broken(tmp_path: Path, targets: LoadTargets) -> None:
    path = tmp_path / FIXTURE_DAY.isoformat() / "raw_poll.jsonl"
    path.parent.mkdir()
    path.write_bytes(b"[" * 100_000 + b"]" * 100_000 + b"\n")
    batch = read_day(tmp_path, FIXTURE_DAY, include_trial=True, targets=targets)
    assert batch.stats.broken_lines == 1
