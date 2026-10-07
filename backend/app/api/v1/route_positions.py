"""GET /routes/{routeId}/positions(계약 4.8절)."""

from typing import Annotated

from fastapi import APIRouter, Depends, Path

from app.api.deps import get_route_position_service, limit_default_get
from app.api.responses import error_responses
from app.schemas.common import ROUTE_ID_PATTERN
from app.schemas.route_positions import RoutePositionsResponse
from app.services.route_positions import RoutePositionService

router = APIRouter(tags=["routes"], dependencies=[Depends(limit_default_get)])


@router.get(
    "/routes/{routeId}/positions",
    response_model=RoutePositionsResponse,
    responses=error_responses(400, 404, 429, 500),
    summary="노선 정류장 좌표와 최신 수집 차량 위치·잔여석(지도)",
)
def get_route_positions(
    # 인자 이름이 곧 경로 변수 이름이라 계약대로 camelCase 로 둔다.
    routeId: Annotated[
        str, Path(pattern=ROUTE_ID_PATTERN, description="GBIS 노선 ID(숫자 1–20자리)")
    ],
    service: Annotated[RoutePositionService, Depends(get_route_position_service)],
) -> RoutePositionsResponse:
    """형식이 틀리면 400, 지원하지 않는 노선(정류장 좌표 기준정보 없음 포함)이면 404.

    수집기가 저장한 파일만 읽고 GBIS 를 부르지 않는다. 과거 시각 지정은 없다.
    """
    return service.get_positions(routeId)
