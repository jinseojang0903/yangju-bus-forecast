"""GET /stations, GET /stations/{stationId}/routes 응답(계약 4.3절)."""

from app.schemas.common import ApiModel


class StationItem(ApiModel):
    station_id: str
    name: str
    direction_label: str
    mobile_no: str | None


class StationListResponse(ApiModel):
    items: list[StationItem]


class RouteItem(ApiModel):
    route_id: str
    route_name: str
    alight_station_id: str
    alight_station_name: str


class DestinationItem(ApiModel):
    destination_id: str
    name: str
    routes: list[RouteItem]


class StationRoutesResponse(ApiModel):
    station_id: str
    destinations: list[DestinationItem]
