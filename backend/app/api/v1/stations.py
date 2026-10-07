"""GET /stations, GET /stations/{stationId}/routes(계약 4.3절)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import get_station_service, limit_default_get
from app.api.responses import error_responses
from app.schemas.common import STATION_ID_PATTERN
from app.schemas.stations import StationListResponse, StationRoutesResponse
from app.services.stations import StationService

router = APIRouter(tags=["stations"], dependencies=[Depends(limit_default_get)])


@router.get(
    "/stations",
    response_model=StationListResponse,
    responses=error_responses(429, 500),
    summary="출발 정류장 목록",
)
def list_stations(
    service: Annotated[StationService, Depends(get_station_service)],
) -> StationListResponse:
    return service.list_stations()


@router.get(
    "/stations/{stationId}/routes",
    response_model=StationRoutesResponse,
    responses=error_responses(400, 404, 429, 500),
    summary="정류장의 노선·목적지",
)
def get_station_routes(
    # 인자 이름이 곧 경로 변수 이름이라 계약대로 camelCase 로 둔다.
    stationId: Annotated[
        str, Path(pattern=STATION_ID_PATTERN, description="GBIS 정류소 ID(숫자 1–20자리)")
    ],
    service: Annotated[StationService, Depends(get_station_service)],
) -> StationRoutesResponse:
    """형식이 틀리면 400, 없는 정류장이면 404."""
    return service.get_routes(stationId)
