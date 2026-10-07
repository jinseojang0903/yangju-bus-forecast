"""GET /snapshot, GET /snapshot/{snapshotId}/explanation(계약 4.4·4.5절, 12장)."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Path, Query

from app.api.deps import (
    get_app_settings,
    get_snapshot_service,
    limit_explanation,
    limit_snapshot,
)
from app.api.responses import error_responses
from app.core.errors import ValidationFailedError
from app.core.settings import Settings
from app.schemas.common import DESTINATION_ID_PATTERN, STATION_ID_PATTERN, TIME_OF_DAY_PATTERN
from app.schemas.snapshot import SNAPSHOT_SCENARIOS, ExplanationResponse, SnapshotResponse
from app.services.snapshot import SnapshotService

router = APIRouter(tags=["snapshot"])

_SCENARIO_DESCRIPTION = (
    "개발·시연용(계약 12장). FAKE_DATA=true 일 때만 받고, 아니면 무시한다. "
    "허용 값: " + ", ".join(SNAPSHOT_SCENARIOS)
)


def _allowed_scenario(scenario: str | None, settings: Settings) -> str | None:
    """FAKE_DATA=false 면 무시(None). true 면 허용 목록 밖의 값은 400."""
    if not settings.fake_data or scenario is None:
        return None
    if scenario not in SNAPSHOT_SCENARIOS:
        raise ValidationFailedError([{"field": "scenario", "reason": "허용된 값이 아닙니다"}])
    return scenario


@router.get(
    "/snapshot",
    response_model=SnapshotResponse,
    responses=error_responses(400, 404, 429, 500),
    dependencies=[Depends(limit_snapshot)],
    summary="현재 버스·0석 위험·대안(같은 계산 시점)",
)
def get_snapshot(
    station: Annotated[
        str, Query(pattern=STATION_ID_PATTERN, description="출발 정류장 ID. 예: 235000392")
    ],
    destination: Annotated[
        str, Query(pattern=DESTINATION_ID_PATTERN, description="목적지 코드. 예: jamsil")
    ],
    service: Annotated[SnapshotService, Depends(get_snapshot_service)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    deadline: Annotated[
        str | None,
        Query(pattern=TIME_OF_DAY_PATTERN, description="목적지 도착 마감 HH:MM(KST)"),
    ] = None,
    scenario: Annotated[str | None, Query(description=_SCENARIO_DESCRIPTION)] = None,
) -> SnapshotResponse:
    """형식이 틀리면 400, 정류장·목적지가 없으면 404. GBIS 를 부르지 않는다."""
    return service.get_snapshot(
        station, destination, deadline, _allowed_scenario(scenario, settings)
    )


@router.get(
    "/snapshot/{snapshotId}/explanation",
    response_model=ExplanationResponse,
    responses=error_responses(400, 404, 429, 500),
    dependencies=[Depends(limit_explanation)],
    summary="스냅샷 설명(뼈대 단계: 항상 고정 문구)",
)
def get_explanation(
    # 인자 이름이 곧 경로 변수 이름이라 계약대로 camelCase 로 둔다.
    snapshotId: Annotated[UUID, Path(description="스냅샷 ID(uuid)")],
    service: Annotated[SnapshotService, Depends(get_snapshot_service)],
) -> ExplanationResponse:
    """uuid 형식이 아니면 400, 스냅샷이 없거나 만료됐으면 404."""
    return service.get_explanation(snapshotId)
