"""가짜 적재 저장소와 시험 도우미.

PostgreSQL 키 규칙(ON CONFLICT DO NOTHING·업서트)과 트랜잭션 롤백을 흉내 낸다.
"""

import copy
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import Path
from typing import Any

from app.loader.reference import RouteRow, RouteStationRow, StationRow
from app.loader.rows import BusArrivalRow, BusPositionRow, RawPollRow, ServiceDayRow
from app.loader.store import RawPollInsert

FIXTURE_DATA_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "loader"
FIXTURE_DAY = date(2026, 10, 7)
REFERENCE_DAY = date(2026, 10, 6)


class InjectedFailure(RuntimeError):
    pass


@dataclass
class FakeState:
    service_days: dict[date, ServiceDayRow] = field(default_factory=dict)
    raw_polls: dict[tuple[str, int], tuple[int, RawPollRow]] = field(default_factory=dict)
    positions: dict[tuple[int, int], BusPositionRow] = field(default_factory=dict)
    arrivals: dict[tuple[int, int, int], BusArrivalRow] = field(default_factory=dict)
    routes: dict[str, RouteRow] = field(default_factory=dict)
    stations: dict[str, StationRow] = field(default_factory=dict)
    route_stations: dict[tuple[str, int], RouteStationRow] = field(default_factory=dict)
    next_id: int = 1


class FakeWriter:
    def __init__(self, state: FakeState, fail_on: str | None) -> None:
        self._state = state
        self._fail_on = fail_on

    def _maybe_fail(self, step: str) -> None:
        if self._fail_on == step:
            raise InjectedFailure(step)

    def upsert_service_day(self, row: ServiceDayRow) -> None:
        self._maybe_fail("service_day")
        old = self._state.service_days.get(row.service_date)
        if old is not None and row.operation_kind == "none":
            row = ServiceDayRow(
                row.service_date, row.is_weekday, row.is_holiday, old.operation_kind
            )
        self._state.service_days[row.service_date] = row

    def insert_raw_polls(self, jsonl_file: str, rows: Sequence[RawPollRow]) -> RawPollInsert:
        self._maybe_fail("raw_poll")
        inserted = 0
        for row in rows:
            key = (jsonl_file, row.line_no)
            if key not in self._state.raw_polls:
                self._state.raw_polls[key] = (self._state.next_id, row)
                self._state.next_id += 1
                inserted += 1
        ids = {row.line_no: self._state.raw_polls[(jsonl_file, row.line_no)][0] for row in rows}
        return RawPollInsert(ids=ids, inserted=inserted)

    def insert_bus_positions(self, rows: Sequence[tuple[int, BusPositionRow]]) -> int:
        self._maybe_fail("bus_position")
        inserted = 0
        for raw_poll_id, row in rows:
            key = (raw_poll_id, row.item_index)
            if key not in self._state.positions:
                self._state.positions[key] = row
                inserted += 1
        return inserted

    def insert_bus_arrivals(self, rows: Sequence[tuple[int, BusArrivalRow]]) -> int:
        self._maybe_fail("bus_arrival")
        inserted = 0
        for raw_poll_id, row in rows:
            key = (raw_poll_id, row.item_index, row.arrival_rank)
            if key not in self._state.arrivals:
                self._state.arrivals[key] = row
                inserted += 1
        return inserted

    def upsert_routes(self, rows: Sequence[RouteRow]) -> int:
        self._maybe_fail("route")
        for row in rows:
            self._state.routes[row.route_id] = row
        return len(rows)

    def upsert_stations(self, rows: Sequence[StationRow]) -> int:
        self._maybe_fail("station")
        for row in rows:
            old = self._state.stations.get(row.station_id)
            if old is not None:  # COALESCE(EXCLUDED.col, station.col)
                row = replace(
                    row,
                    mobile_no=row.mobile_no if row.mobile_no is not None else old.mobile_no,
                    region_name=row.region_name if row.region_name is not None else old.region_name,
                    x=row.x if row.x is not None else old.x,
                    y=row.y if row.y is not None else old.y,
                )
            self._state.stations[row.station_id] = row
        return len(rows)

    def upsert_route_stations(self, rows: Sequence[RouteStationRow]) -> int:
        self._maybe_fail("route_station")
        for row in rows:
            if row.route_id not in self._state.routes or row.station_id not in self._state.stations:
                raise InjectedFailure("FK")
            self._state.route_stations[(row.route_id, row.station_seq)] = row
        return len(rows)

    def delete_route_stations_except(self, route_id: str, keep_seqs: Sequence[int]) -> list[int]:
        self._maybe_fail("route_station_delete")
        if not keep_seqs:
            return []
        keep = set(keep_seqs)
        stale = sorted(
            k for k in self._state.route_stations if k[0] == route_id and k[1] not in keep
        )
        for key in stale:
            del self._state.route_stations[key]
        return [seq for _, seq in stale]


class FakeStore:
    """transaction() 블록에서 예외가 나면 블록 전 상태로 되돌린다."""

    def __init__(self, state: FakeState | None = None, fail_on: str | None = None) -> None:
        self.state = state or FakeState()
        self.fail_on = fail_on
        self.closed = False
        self.transactions = 0

    @contextmanager
    def transaction(self) -> Iterator[FakeWriter]:
        snapshot = copy.deepcopy(self.state)
        try:
            yield FakeWriter(self.state, self.fail_on)
        except BaseException:
            self.state.__dict__.update(snapshot.__dict__)
            raise
        self.transactions += 1

    def close(self) -> None:
        self.closed = True

    def counts(self) -> dict[str, Any]:
        return {
            "service_day": len(self.state.service_days),
            "raw_poll": len(self.state.raw_polls),
            "bus_position": len(self.state.positions),
            "bus_arrival": len(self.state.arrivals),
        }


def copy_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """픽스처 JSONL 을 tmp 데이터 폴더로 복사한다(덧붙이기 시험용). (데이터 폴더, 파일)."""
    data_dir = tmp_path / "collected"
    target = data_dir / FIXTURE_DAY.isoformat() / "raw_poll.jsonl"
    target.parent.mkdir(parents=True)
    source = FIXTURE_DATA_DIR / FIXTURE_DAY.isoformat() / "raw_poll.jsonl"
    target.write_bytes(source.read_bytes())
    return data_dir, target


# 줄바꿈 없이 덧붙여 '수집기가 쓰는 중인 마지막 줄'을 흉내 낸다(G1300 위치 1대).
PARTIAL_LINE = (
    '{"collected_at":"2026-10-07T06:01:00.001+09:00","api":"getBusLocationListv2",'
    '"params":{"routeId":"235000092","format":"json"},"http_status":200,"ok":true,'
    '"result_code":"0","is_holiday":false,"mode":"run","interval_sec":10,'
    '"body":{"response":{"msgBody":{"busLocationList":[{"routeId":235000092,'
    '"vehId":235000101,"stationSeq":14,"stateCd":0,"remainSeatCnt":0}]}}}}'
)
