"""노선 정류장 좌표와 최신 수집 차량 위치(계약 4.8절, F02 지도).

이용자 요청 경로에서 GBIS 를 부르지 않는다. 수집기가 쓴 파일과 10초 캐시로만 답한다.
과거 시각을 지정하는 기능은 없다(계약 2.2절).
"""

from datetime import datetime

from app.collector.schedule import is_in_window, next_window_start, to_kst
from app.core.errors import NotFoundError
from app.core.settings import (
    ROUTE_POSITIONS_INCLUDE_TRIAL,
    ROUTE_POSITIONS_LOOKBACK_DAYS,
    ROUTE_POSITIONS_ROUTES,
    ROUTE_POSITIONS_TARGET_STATION_ID,
    TargetRoute,
)
from app.forecast.service_time import is_stale, next_collect_boundary_at
from app.labeling.records import PositionRecord
from app.repositories.route_positions import RoutePositionRepository, RouteStations
from app.schemas.common import VehicleState
from app.schemas.route_positions import (
    RoutePositionsResponse,
    RouteStationItem,
    VehiclePositionItem,
)
from app.services.cache import TtlCache

_STATE_BY_CODE: dict[int, VehicleState] = {1: "arrived", 2: "departed", 0: "passing"}
_SUPPORTED_ROUTES: dict[str, TargetRoute] = {
    route.route_id: route for route in ROUTE_POSITIONS_ROUTES if route.route_id
}


def vehicle_state(state_cd: int | None) -> VehicleState:
    """GBIS stateCd → 계약 상태. 1 도착, 2 출발, 0 교차로 통과, 그 밖(없음 포함) unknown."""
    if state_cd is None:
        return "unknown"
    return _STATE_BY_CODE.get(state_cd, "unknown")


def remain_seats(value: int | None) -> int | None:
    """잔여석. -1·없음·음수는 '정보 없음'(None)이며 0석과 구분한다."""
    return value if value is not None and value >= 0 else None


def target_seq(stations: RouteStations, target_station_id: str) -> int | None:
    """잠실행 구간(회차 순번 이하)에서 내 정류장의 순번. 노선에 없으면 None."""
    for station in stations.stations:
        if station.station_id == target_station_id and station.station_seq <= stations.turn_seq:
            return station.station_seq
    return None


def stops_to_target(vehicle_seq: int, target: int | None, state: VehicleState) -> int | None:
    """내 정류장까지 남은 정류장 수. 잠실행 구간에서 내 정류장 앞(같은 순번 포함)일 때만.

    내 정류장은 회차 순번 이하이므로 차량 순번 ≤ 내 정류장 순번이면 잠실행 구간이다.
    내 정류장 순번에서 '출발'(stateCd 2)이면 이미 떠난 차라서 null 이다.
    """
    if target is None or vehicle_seq > target:
        return None
    if vehicle_seq == target and state == "departed":
        return None
    return target - vehicle_seq


class RoutePositionService:
    def __init__(
        self,
        repository: RoutePositionRepository,
        cache: TtlCache[RoutePositionsResponse | None],
        now: datetime,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._now = to_kst(now)

    def _cache_ttl(self, response: RoutePositionsResponse | None) -> float | None:
        """캐시 유지 시간 = min(TTL, nextRefreshAt − 지금). 기준정보 없음(None)은 캐시 안 함."""
        if response is None:
            return None
        return (response.next_refresh_at - self._now).total_seconds()

    def get_positions(self, route_id: str) -> RoutePositionsResponse:
        """노선의 정류장 좌표와 최신 차량 위치.

        route_id 는 경계에서 숫자 형식을 확인한 값이다. 지원 노선이 아니거나 정류장 좌표
        기준정보가 없으면 NotFoundError(404). 위치 기록이 없으면 vehicles 는 빈 배열이고
        dataUpdatedAt 은 null 이다.
        """
        route = _SUPPORTED_ROUTES.get(route_id)
        if route is None:
            raise NotFoundError()
        response = self._cache.get_or_set(
            route_id, lambda: self._compute(route_id, route), self._cache_ttl
        )
        if response is None:
            raise NotFoundError()
        return response

    def _compute(self, route_id: str, route: TargetRoute) -> RoutePositionsResponse | None:
        stations = self._repository.load_route_stations(route_id)
        if stations is None:
            return None
        latest = self._repository.find_latest_positions(
            route_id,
            self._now.date(),
            lookback_days=ROUTE_POSITIONS_LOOKBACK_DAYS,
            include_trial=ROUTE_POSITIONS_INCLUDE_TRIAL,
        )
        data_updated_at = latest.collected_at if latest else None
        in_window = is_in_window(self._now)
        target = target_seq(stations, ROUTE_POSITIONS_TARGET_STATION_ID)
        station_id_by_seq = {s.station_seq: s.station_id for s in stations.stations}
        return RoutePositionsResponse(
            route_id=route_id,
            route_name=route.route_name,
            target_station_id=ROUTE_POSITIONS_TARGET_STATION_ID,
            computed_at=self._now,
            data_updated_at=data_updated_at,
            stale=in_window and is_stale(self._now, data_updated_at),
            in_collection_window=in_window,
            next_refresh_at=self._next_refresh_at(in_window, data_updated_at, route),
            stations=[
                RouteStationItem(
                    station_seq=s.station_seq,
                    station_id=s.station_id,
                    name=s.name,
                    lat=s.lat,
                    lng=s.lng,
                    is_outbound=s.station_seq <= stations.turn_seq,
                    is_target=s.station_id == ROUTE_POSITIONS_TARGET_STATION_ID,
                )
                for s in stations.stations
            ],
            vehicles=self._vehicles(latest.records if latest else (), station_id_by_seq, target),
        )

    def _next_refresh_at(
        self, in_window: bool, data_updated_at: datetime | None, route: TargetRoute
    ) -> datetime:
        """계약 1.3절 규칙을 이 노선의 수집 주기로 적용한다. 수집 시간 밖이면 다음 수집 시작."""
        if not in_window:
            return next_window_start(self._now)
        return next_collect_boundary_at(self._now, data_updated_at, interval_sec=route.interval_sec)

    @staticmethod
    def _vehicles(
        records: tuple[PositionRecord, ...],
        station_id_by_seq: dict[int, str],
        target: int | None,
    ) -> list[VehiclePositionItem]:
        vehicles: list[VehiclePositionItem] = []
        for record in records:
            # 위치 응답에 stationId 가 없으면 기준정보의 같은 순번 정류장으로 채운다.
            station_id = record.station_id or station_id_by_seq.get(record.station_seq)
            if station_id is None:
                continue  # 지도에 놓을 수 없다
            state = vehicle_state(record.state_cd)
            vehicles.append(
                VehiclePositionItem(
                    vehicle_id=record.veh_id,
                    plate_no=record.plate_no,
                    station_seq=record.station_seq,
                    station_id=station_id,
                    state=state,
                    remain_seats=remain_seats(record.remain_seat_cnt),
                    stops_to_target=stops_to_target(record.station_seq, target, state),
                )
            )
        vehicles.sort(key=lambda v: (v.station_seq, v.vehicle_id))
        return vehicles
