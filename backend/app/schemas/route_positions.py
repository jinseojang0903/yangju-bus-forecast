"""GET /routes/{routeId}/positions 응답(계약 4.8절)."""

from datetime import datetime

from app.schemas.common import ApiModel, VehicleState


class RouteStationItem(ApiModel):
    station_seq: int
    station_id: str
    name: str
    lat: float  # WGS84 위도(GBIS y)
    lng: float  # WGS84 경도(GBIS x)
    is_outbound: bool  # 잠실행 구간(stationSeq ≤ 회차 순번)
    is_target: bool


class VehiclePositionItem(ApiModel):
    vehicle_id: str
    plate_no: str | None
    station_seq: int
    station_id: str
    state: VehicleState
    remain_seats: int | None  # -1·빈값·음수면 None('정보 없음', 0석 아님)
    stops_to_target: int | None


class RoutePositionsResponse(ApiModel):
    route_id: str
    route_name: str
    target_station_id: str
    computed_at: datetime
    data_updated_at: datetime | None
    stale: bool
    in_collection_window: bool
    next_refresh_at: datetime
    stations: list[RouteStationItem]
    vehicles: list[VehiclePositionItem]
