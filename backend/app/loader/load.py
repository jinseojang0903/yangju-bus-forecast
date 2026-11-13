"""하루 묶음·기준정보 묶음을 한 트랜잭션으로 저장소에 쓴다.

순서: ① service_day ② raw_poll ③ bus_position ④ bus_arrival. 도중에 예외가 나면 그날 전체가
롤백된다(store.transaction). 같은 묶음을 몇 번 써도 결과가 같다(키가 있으면 넣지 않음).
"""

from dataclasses import dataclass

from app.loader.jsonl import DayBatch
from app.loader.reference import ReferenceBatch
from app.loader.store import LoadError, LoaderStore


@dataclass(frozen=True)
class DayLoadResult:
    raw_poll_inserted: int
    raw_poll_existing: int
    positions_inserted: int
    arrivals_inserted: int


@dataclass(frozen=True)
class ReferenceLoadResult:
    routes: int
    stations: int
    route_stations: int
    deleted_seqs: dict[str, list[int]]  # 노선 routeId → 지운 순번(기준정보에서 없어진 순번)
    trim_skipped_routes: list[str]  # 건너뛴 항목이 있어 순번 정리(삭제)를 하지 않은 노선

    @property
    def route_stations_deleted(self) -> int:
        return sum(len(seqs) for seqs in self.deleted_seqs.values())


def write_day(store: LoaderStore, batch: DayBatch) -> DayLoadResult:
    """그날 행을 넣는다. 실패하면 예외를 올리고 그날 쓴 것은 모두 되돌려진다."""
    with store.transaction() as writer:
        writer.upsert_service_day(batch.service_day)
        raw_rows = [line.raw_poll for line in batch.lines]
        inserted = writer.insert_raw_polls(batch.jsonl_file, raw_rows)
        missing = [row.line_no for row in raw_rows if row.line_no not in inserted.ids]
        if missing:
            raise LoadError(f"raw_poll id 를 찾지 못한 줄이 {len(missing)}개 있다")
        positions = [
            (inserted.ids[line.raw_poll.line_no], row)
            for line in batch.lines
            for row in line.positions
        ]
        arrivals = [
            (inserted.ids[line.raw_poll.line_no], row)
            for line in batch.lines
            for row in line.arrivals
        ]
        positions_inserted = writer.insert_bus_positions(positions)
        arrivals_inserted = writer.insert_bus_arrivals(arrivals)
    return DayLoadResult(
        raw_poll_inserted=inserted.inserted,
        raw_poll_existing=len(raw_rows) - inserted.inserted,
        positions_inserted=positions_inserted,
        arrivals_inserted=arrivals_inserted,
    )


def write_reference(store: LoaderStore, batch: ReferenceBatch) -> ReferenceLoadResult:
    """route → station → route_station 순으로 업서트한다(FK 순서). 한 트랜잭션.

    정류장 목록을 받은 노선은 목록에 없는 순번 행을 지워 기준정보와 맞춘다. 단, 그 노선 목록에
    건너뛴 항목이 있으면(목록이 불완전할 수 있음) 지우지 않고 trim_skipped_routes 로 알린다.
    """
    groups = batch.route_station_groups()
    deleted: dict[str, list[int]] = {}
    trim_skipped: list[str] = []
    with store.transaction() as writer:
        routes = writer.upsert_routes(batch.routes)
        stations = writer.upsert_stations(batch.stations)
        for route_id in sorted(groups):
            rows = groups[route_id]
            writer.upsert_route_stations(rows)
            if not batch.can_trim(route_id):
                trim_skipped.append(route_id)
                continue
            removed = writer.delete_route_stations_except(route_id, [r.station_seq for r in rows])
            if removed:
                deleted[route_id] = removed
    return ReferenceLoadResult(
        routes=routes,
        stations=stations,
        route_stations=len(batch.route_stations),
        deleted_seqs=deleted,
        trim_skipped_routes=trim_skipped,
    )
