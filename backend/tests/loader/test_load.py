"""가짜 저장소로 하루 적재의 멱등(두 번 넣어도 같음)·이어 넣기·롤백을 확인한다."""

from dataclasses import replace
from pathlib import Path

import pytest

from app.loader.jsonl import read_day
from app.loader.load import write_day, write_reference
from app.loader.reference import read_reference
from app.loader.rows import LoadTargets
from tests.loader.fakes import (
    FIXTURE_DATA_DIR,
    FIXTURE_DAY,
    PARTIAL_LINE,
    REFERENCE_DAY,
    FakeStore,
    InjectedFailure,
    copy_fixture,
)

TARGETS = LoadTargets.from_settings()
EXPECTED = {"service_day": 1, "raw_poll": 8, "bus_position": 5, "bus_arrival": 3}


def _batch(data_dir: Path = FIXTURE_DATA_DIR):
    return read_day(data_dir, FIXTURE_DAY, include_trial=True, targets=TARGETS)


def test_load_twice_gives_same_rows() -> None:
    store = FakeStore()
    first = write_day(store, _batch())
    assert store.counts() == EXPECTED
    assert (first.raw_poll_inserted, first.raw_poll_existing) == (8, 0)
    assert (first.positions_inserted, first.arrivals_inserted) == (5, 3)

    second = write_day(store, _batch())
    assert store.counts() == EXPECTED
    assert (second.raw_poll_inserted, second.raw_poll_existing) == (0, 8)
    assert (second.positions_inserted, second.arrivals_inserted) == (0, 0)
    service_day = store.state.service_days[FIXTURE_DAY]
    assert (service_day.is_weekday, service_day.is_holiday, service_day.operation_kind) == (
        True,
        False,
        "regular",
    )


def test_children_point_to_their_raw_poll() -> None:
    store = FakeStore()
    write_day(store, _batch())
    line_by_id = {raw_id: row.line_no for (_, _), (raw_id, row) in store.state.raw_polls.items()}
    for (raw_id, _), position in store.state.positions.items():
        assert line_by_id[raw_id] == position.line_no
    for (raw_id, _, _), arrival in store.state.arrivals.items():
        assert line_by_id[raw_id] == arrival.line_no


def test_partial_line_is_loaded_on_next_run(tmp_path: Path) -> None:
    data_dir, path = copy_fixture(tmp_path)
    with path.open("ab") as fp:
        fp.write(PARTIAL_LINE.encode("utf-8"))
    store = FakeStore()
    write_day(store, _batch(data_dir))
    assert store.counts() == EXPECTED

    with path.open("ab") as fp:
        fp.write(b"\n")
    result = write_day(store, _batch(data_dir))
    assert (result.raw_poll_inserted, result.raw_poll_existing) == (1, 8)
    assert result.positions_inserted == 1
    assert store.counts()["raw_poll"] == 9
    assert (f"{FIXTURE_DAY.isoformat()}/raw_poll.jsonl", 15) in store.state.raw_polls


@pytest.mark.parametrize("step", ["service_day", "raw_poll", "bus_position", "bus_arrival"])
def test_failure_rolls_back_whole_day(step: str) -> None:
    store = FakeStore(fail_on=step)
    with pytest.raises(InjectedFailure):
        write_day(store, _batch())
    assert store.counts() == {"service_day": 0, "raw_poll": 0, "bus_position": 0, "bus_arrival": 0}


def test_failure_keeps_earlier_load_intact() -> None:
    store = FakeStore()
    write_day(store, _batch())
    store.fail_on = "bus_arrival"
    with pytest.raises(InjectedFailure):
        write_day(store, _batch())
    assert store.counts() == EXPECTED


def test_missing_file_does_not_overwrite_regular_day(tmp_path: Path) -> None:
    store = FakeStore()
    write_day(store, _batch())
    empty = read_day(tmp_path, FIXTURE_DAY, include_trial=True, targets=TARGETS)
    write_day(store, empty)
    assert store.state.service_days[FIXTURE_DAY].operation_kind == "regular"


def test_reference_upsert_is_idempotent() -> None:
    batch = read_reference(FIXTURE_DATA_DIR / "reference" / "2026-10-06", REFERENCE_DAY)
    store = FakeStore()
    first = write_reference(store, batch)
    second = write_reference(store, batch)
    assert (first.routes, first.stations, first.route_stations) == (18, 6, 7)
    # G1300 목록에는 이름 없는 항목(건너뜀)이 있어 순번 정리를 하지 않는다.
    assert first.trim_skipped_routes == ["235000092"]
    assert first.deleted_seqs == {}
    assert second == first
    assert len(store.state.routes) == 18
    assert len(store.state.stations) == 6
    assert len(store.state.route_stations) == 7


def test_reference_trims_only_complete_route_lists() -> None:
    batch = read_reference(FIXTURE_DATA_DIR / "reference" / "2026-10-06", REFERENCE_DAY)
    store = FakeStore()
    write_reference(store, batch)
    # 예전 기준정보에만 있던 순번(9)을 두 노선에 넣어 둔다.
    for route_id in ("235000092", "235000123"):
        old = store.state.route_stations[(route_id, 1)]
        store.state.route_stations[(route_id, 9)] = replace(old, station_seq=9)

    result = write_reference(store, batch)
    assert result.deleted_seqs == {"235000123": [9]}
    assert result.route_stations_deleted == 1
    assert ("235000123", 9) not in store.state.route_stations
    assert ("235000092", 9) in store.state.route_stations  # 목록이 불완전해 지우지 않음


def test_delete_with_empty_keep_list_deletes_nothing() -> None:
    batch = read_reference(FIXTURE_DATA_DIR / "reference" / "2026-10-06", REFERENCE_DAY)
    store = FakeStore()
    write_reference(store, batch)
    with store.transaction() as writer:
        assert writer.delete_route_stations_except("235000123", []) == []
    assert len(store.state.route_stations) == 7


def test_station_upsert_keeps_existing_values_for_nulls() -> None:
    batch = read_reference(FIXTURE_DATA_DIR / "reference" / "2026-10-06", REFERENCE_DAY)
    store = FakeStore()
    write_reference(store, batch)
    board = store.state.stations["235000392"]
    with store.transaction() as writer:
        writer.upsert_stations([replace(board, mobile_no=None, x=None, station_name="새 이름")])
    updated = store.state.stations["235000392"]
    assert (updated.station_name, updated.mobile_no, updated.x) == ("새 이름", "39624", 127.07)


def test_reference_rollback_on_failure() -> None:
    batch = read_reference(FIXTURE_DATA_DIR / "reference" / "2026-10-06", REFERENCE_DAY)
    store = FakeStore(fail_on="route_station")
    with pytest.raises(InjectedFailure):
        write_reference(store, batch)
    assert store.state.routes == {} and store.state.stations == {}
