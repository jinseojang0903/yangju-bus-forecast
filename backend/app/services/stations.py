"""정류장·노선 조회(계약 4.3절, F01)."""

from app.core.errors import NotFoundError
from app.repositories.base import ForecastRepository
from app.schemas.stations import StationListResponse, StationRoutesResponse


class StationService:
    def __init__(self, repository: ForecastRepository) -> None:
        self._repository = repository

    def list_stations(self) -> StationListResponse:
        return StationListResponse(items=self._repository.list_stations())

    def get_routes(self, station_id: str) -> StationRoutesResponse:
        """정류장의 목적지·노선. 정류장이 없으면 NotFoundError."""
        routes = self._repository.get_station_routes(station_id)
        if routes is None:
            raise NotFoundError()
        return routes
