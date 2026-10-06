"""JSONL 위치 기록 읽기: 실제 응답 형식, 관대한 숫자 해석, 줄 단위 집계."""

import json
from pathlib import Path

from app.core.settings import KST
from app.labeling.records import extract_positions, load_raw_poll
from tests.labeling.helpers import (
    G1300,
    arrival_line,
    at,
    gbis_item,
    location_line,
    write_jsonl,
)

REAL_FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "gbis"
    / "getBusLocationListv2_routeId-235000092_20261006_093619_real.json"
)


def test_extracts_every_vehicle_from_real_response() -> None:
    body = json.loads(REAL_FIXTURE.read_text(encoding="utf-8"))
    line = location_line(at(9, 36, 19), [], route_id=G1300)
    line["body"] = body

    records, skipped = extract_positions(line)

    assert skipped == 0
    assert len(records) == len(body["response"]["msgBody"]["busLocationList"])
    first = records[0]
    assert first.route_id == G1300
    assert first.veh_id == "235000359"
    assert (first.station_seq, first.state_cd, first.remain_seat_cnt) == (6, 2, 39)
    assert first.station_id == "277104470"
    assert first.plate_no == "경기76바8260"
    assert first.route_type_cd == "11"
    assert first.collected_at == at(9, 36, 19)
    assert first.collected_at.tzinfo is not None


def test_reads_numeric_strings_and_skips_items_without_ids() -> None:
    items = [
        {"vehId": "235000001", "stationSeq": "12", "stateCd": "2", "remainSeatCnt": "-1"},
        {"stationSeq": 5, "stateCd": 0, "remainSeatCnt": 3},  # vehId 없음
        {"vehId": 235000002, "stateCd": 0},  # stationSeq 없음
    ]
    records, skipped = extract_positions(location_line(at(6, 0), items, route_id=G1300))
    assert skipped == 2
    assert len(records) == 1
    record = records[0]
    assert record.route_id == G1300  # 항목에 없으면 요청 파라미터에서
    assert (record.station_seq, record.state_cd, record.remain_seat_cnt) == (12, 2, -1)
    assert record.station_id is None


def test_ignores_other_apis_and_failed_responses() -> None:
    assert extract_positions(arrival_line(at(6, 0))) == ([], 0)
    failed = location_line(
        at(6, 0), [gbis_item("235000001", 3, 0, 5, route_id=G1300)], route_id=G1300, ok=False
    )
    assert extract_positions(failed) == ([], 0)


def test_load_raw_poll_filters_trial_and_counts_lines(tmp_path: Path) -> None:
    item = gbis_item("235000001", 12, 2, 4, route_id=G1300)
    legacy = location_line(at(9, 36), [item], route_id=G1300, mode="once")
    del legacy["interval_sec"]  # 간격 필드가 생기기 전 형식
    path = write_jsonl(
        tmp_path / "raw_poll.jsonl",
        [
            legacy,
            location_line(at(10, 0), [item], route_id=G1300),
            location_line(at(10, 30), [item], route_id=G1300, mode="trial", interval_sec=40),
            arrival_line(at(10, 0)),
            location_line(at(10, 1), [item], route_id=G1300, ok=False),
            '{"collected_at":"2026-10-06T10:02:00',  # 수집기가 줄 중간에 죽은 경우
        ],
    )

    records, stats = load_raw_poll(path, include_trial=False)
    assert len(records) == 2
    assert [r.interval_sec for r in records] == [None, 60]
    assert (stats.lines, stats.location_lines, stats.broken_lines) == (6, 4, 1)
    assert (stats.trial_lines_skipped, stats.failed_location_lines) == (1, 1)
    assert stats.items == 2
    assert stats.interval_secs == {None, 60}

    with_trial, stats_trial = load_raw_poll(path, include_trial=True)
    assert len(with_trial) == 3
    assert stats_trial.trial_lines_skipped == 0
    trial_record = with_trial[-1]
    assert trial_record.is_trial
    assert trial_record.interval_sec == 40
    assert trial_record.collected_at.tzinfo == KST


def test_no_result_response_is_not_a_failure(tmp_path: Path) -> None:
    line = location_line(at(6, 0), [], route_id=G1300)
    line["result_code"] = "4"
    line["body"] = {"response": {"msgHeader": {"resultCode": 4}}}
    path = write_jsonl(tmp_path / "raw_poll.jsonl", [line])
    records, stats = load_raw_poll(path, include_trial=False)
    assert records == []
    assert stats.failed_location_lines == 0
