"""의존성 주입(설정·시계·저장소·서비스·호출 제한). 가짜 → DB 저장소 교체 지점은 get_repository."""

import ipaddress
from collections.abc import Awaitable, Callable, Sequence
from datetime import datetime
from typing import Annotated

from fastapi import Depends, Request

from app.core.errors import RateLimitedError
from app.core.ratelimit import RateLimiter
from app.core.settings import API_RATE_LIMITS, KST, RateLimitRule, Settings
from app.repositories.base import ForecastRepository
from app.repositories.collector_status import CollectorStatusRepository
from app.repositories.route_positions import RoutePositionRepository
from app.schemas.route_positions import RoutePositionsResponse
from app.schemas.snapshot import SnapshotResponse
from app.services.cache import TtlCache
from app.services.health import HealthService
from app.services.parse_query import ParseQueryService
from app.services.route_positions import RoutePositionService
from app.services.snapshot import SnapshotService
from app.services.stations import StationService


def get_app_settings(request: Request) -> Settings:
    """create_app 에 넘긴 설정. 테스트는 Settings 를 직접 만들어 넘긴다."""
    return request.app.state.settings


def get_now() -> datetime:
    """지금 시각(KST). 테스트에서 dependency_overrides 로 고정한다."""
    return datetime.now(KST)


def get_repository(request: Request) -> ForecastRepository:
    """예보 저장소. 지금은 FAKE_DATA=true 일 때의 가짜 저장소뿐이다.

    TODO(backend-dev/2026-10-06): DB 저장소(psycopg)를 만들면 FAKE_DATA=false 일 때 그것을
    돌려준다. 그 전까지 FAKE_DATA=false 면 500(INTERNAL_ERROR)으로 답한다.
    """
    repository = request.app.state.repository
    if repository is None:
        raise RuntimeError("FAKE_DATA=false 인데 DB 저장소가 아직 없다")
    return repository


def get_collector_status_repository(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> CollectorStatusRepository:
    return CollectorStatusRepository(settings.data_dir)


def get_health_service(
    repository: Annotated[CollectorStatusRepository, Depends(get_collector_status_repository)],
) -> HealthService:
    return HealthService(repository)


def get_station_service(
    repository: Annotated[ForecastRepository, Depends(get_repository)],
) -> StationService:
    return StationService(repository)


def get_snapshot_service(
    request: Request,
    repository: Annotated[ForecastRepository, Depends(get_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> SnapshotService:
    cache: TtlCache[SnapshotResponse | None] = request.app.state.snapshot_cache
    return SnapshotService(repository, cache, now)


def get_route_position_repository(
    settings: Annotated[Settings, Depends(get_app_settings)],
) -> RoutePositionRepository:
    """노선 지도용 수집 파일 저장소. FAKE_DATA 와 관계없이 실제 수집 파일을 읽는다(계약 4.8절)."""
    return RoutePositionRepository(settings.data_dir)


def get_route_position_service(
    request: Request,
    repository: Annotated[RoutePositionRepository, Depends(get_route_position_repository)],
    now: Annotated[datetime, Depends(get_now)],
) -> RoutePositionService:
    cache: TtlCache[RoutePositionsResponse | None] = request.app.state.route_positions_cache
    return RoutePositionService(repository, cache, now)


def get_parse_query_service() -> ParseQueryService:
    return ParseQueryService()


def client_key(host: str | None) -> str:
    """호출 제한을 셀 클라이언트 키. IPv6 는 /64 로 묶는다(한 가입자가 /64 를 통째로 쓴다).

    IPv4·IPv4 매핑 IPv6 는 주소 그대로, 주소가 아니면(테스트 등) 문자열 그대로 쓴다.
    """
    if not host:
        return "unknown"
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return host
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped is not None:
            return str(address.ipv4_mapped)
        return str(ipaddress.IPv6Network(f"{address}/64", strict=False))
    return str(address)


def _client_key(request: Request) -> str:
    # 프록시 뒤에 두면 uvicorn --proxy-headers 와 --forwarded-allow-ips 로 실제 IP 를 받는다.
    # X-Forwarded-For 를 직접 읽지 않는다(이용자가 위조할 수 있다).
    return client_key(request.client.host if request.client else None)


def rate_limit(bucket: str, rules: Sequence[RateLimitRule]) -> Callable[[Request], Awaitable[None]]:
    """경로 묶음(bucket)별 IP 호출 제한 의존성. 초과하면 RateLimitedError(429)."""

    async def dependency(request: Request) -> None:
        limiter: RateLimiter = request.app.state.rate_limiter
        retry_after = limiter.hit(bucket, rules, _client_key(request))
        if retry_after is not None:
            raise RateLimitedError(retry_after)

    return dependency


limit_snapshot = rate_limit("snapshot", API_RATE_LIMITS.snapshot)
limit_explanation = rate_limit("explanation", API_RATE_LIMITS.explanation)
limit_parse_query = rate_limit("parse_query", API_RATE_LIMITS.parse_query)
limit_default_get = rate_limit("default_get", API_RATE_LIMITS.default_get)
