"""GET /health, GET /health/detail(계약 4.1·4.2절)."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Header

from app.api.deps import get_app_settings, get_health_service, get_now, limit_default_get
from app.api.responses import error_responses
from app.core.errors import NotFoundError
from app.core.security import is_valid_token
from app.core.settings import Settings
from app.schemas.health import HealthDetailResponse, HealthResponse
from app.services.health import HealthService

router = APIRouter(tags=["health"], dependencies=[Depends(limit_default_get)])


@router.get(
    "/health",
    response_model=HealthResponse,
    responses=error_responses(429, 500),
    summary="수집 상태(공개 최소)",
)
def get_health(
    service: Annotated[HealthService, Depends(get_health_service)],
    now: Annotated[datetime, Depends(get_now)],
) -> HealthResponse:
    """수집기 상태 파일만 읽는다. GBIS 를 부르지 않는다."""
    return service.get_health(now)


@router.get(
    "/health/detail",
    response_model=HealthDetailResponse,
    # 경로를 드러내지 않으려고 OpenAPI(/docs)에 싣지 않는다. 명세는 계약 4.2절.
    include_in_schema=False,
)
def get_health_detail(
    service: Annotated[HealthService, Depends(get_health_service)],
    now: Annotated[datetime, Depends(get_now)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_health_token: Annotated[str | None, Header(alias="X-Health-Token")] = None,
) -> HealthDetailResponse:
    """X-Health-Token 이 서버의 HEALTH_DETAIL_TOKEN 과 같을 때만 답한다.

    헤더가 없거나 틀리거나 서버 토큰이 없거나 32자 미만이면, 경로가 없는 것과 같은 404 를 준다.
    """
    configured = settings.health_detail_token.get_secret_value()
    if not is_valid_token(x_health_token, configured):
        raise NotFoundError()
    return service.get_health_detail(now)
