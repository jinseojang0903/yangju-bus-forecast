"""가짜 저장소(FAKE_DATA=true). 실데이터 계산을 붙이기 전까지 정적 응답을 준다(계약 12장).

요청의 deadline 은 형식만 검증하고 쓰지 않는다. scenario 마다 마감이 정해져 있어야
대안 상태(no_alternative 등)를 항상 같은 모양으로 재현할 수 있기 때문이다.
TODO(backend-dev/2026-10-06): DB 저장소를 만들면 deps.get_repository 에서 교체하고 이 파일은
FAKE_DATA=true 에서만 쓴다.
"""

from uuid import UUID

from app.core.settings import COLLECT_TARGET
from app.repositories.fake_scenarios import (
    ALIGHT_STOPS,
    BOARD_STATION_ID,
    BOARD_STATION_MOBILE_NO,
    BOARD_STATION_NAME,
    DESTINATION_ID,
    DESTINATION_NAME,
    build_all_scenarios,
)
from app.schemas.snapshot import DEFAULT_SCENARIO, SnapshotResponse
from app.schemas.stations import (
    DestinationItem,
    RouteItem,
    StationItem,
    StationRoutesResponse,
)


class FakeRepository:
    def __init__(self) -> None:
        self._snapshots_by_scenario = build_all_scenarios()
        self._snapshots_by_id = {s.snapshot_id: s for s in self._snapshots_by_scenario.values()}

    def list_stations(self) -> list[StationItem]:
        return [
            StationItem(
                station_id=BOARD_STATION_ID,
                name=BOARD_STATION_NAME,
                direction_label=COLLECT_TARGET.direction_label,
                mobile_no=BOARD_STATION_MOBILE_NO,
            )
        ]

    def get_station_routes(self, station_id: str) -> StationRoutesResponse | None:
        if station_id != BOARD_STATION_ID:
            return None
        routes = [
            RouteItem(
                route_id=stop.route.route_id or "",
                route_name=stop.route.route_name,
                alight_station_id=stop.station_id,
                alight_station_name=stop.station_name,
            )
            for stop in ALIGHT_STOPS
        ]
        destination = DestinationItem(
            destination_id=DESTINATION_ID, name=DESTINATION_NAME, routes=routes
        )
        return StationRoutesResponse(station_id=station_id, destinations=[destination])

    def get_snapshot(
        self,
        station_id: str,
        destination_id: str,
        deadline: str | None,
        scenario: str | None,
    ) -> SnapshotResponse | None:
        if station_id != BOARD_STATION_ID or destination_id != DESTINATION_ID:
            return None
        return self._snapshots_by_scenario[scenario or DEFAULT_SCENARIO]

    def find_snapshot(self, snapshot_id: UUID) -> SnapshotResponse | None:
        return self._snapshots_by_id.get(snapshot_id)
