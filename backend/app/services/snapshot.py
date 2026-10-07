"""스냅샷과 설명(계약 4.4·4.5절, F02~F04, F08).

이용자 요청 경로에서 GBIS 를 부르지 않는다. 저장소(수집 데이터·가짜)와 10초 캐시로만 답한다.
"""

from datetime import datetime
from uuid import UUID

from app.core.errors import NotFoundError
from app.repositories.base import ForecastRepository
from app.schemas.snapshot import ExplanationResponse, SnapshotResponse
from app.services.cache import TtlCache

# 뼈대 단계 고정 문구(계약 4.5절). 숫자를 담지 않고, 3문장 이내, 단정 표현 없음.
FALLBACK_EXPLANATION_TEXT = (
    "지금은 자동 설명을 제공하지 않아요. 위의 위험 등급과 사례 수를 함께 확인해 주세요."
)


class SnapshotService:
    def __init__(
        self,
        repository: ForecastRepository,
        cache: TtlCache[SnapshotResponse | None],
        now: datetime,
    ) -> None:
        self._repository = repository
        self._cache = cache
        self._now = now

    def _cache_ttl(self, snapshot: SnapshotResponse | None) -> float | None:
        """캐시 유지 시간 = min(TTL, nextRefreshAt − 지금).

        없는 정류장·목적지(None)는 캐시하지 않는다. nextRefreshAt 이 이미 지났으면 0(저장 안 함).
        """
        if snapshot is None:
            return None
        return (snapshot.next_refresh_at - self._now).total_seconds()

    def get_snapshot(
        self,
        station_id: str,
        destination_id: str,
        deadline: str | None,
        scenario: str | None,
    ) -> SnapshotResponse:
        """(정류장, 목적지, 마감)의 스냅샷. 정류장·목적지가 없으면 NotFoundError.

        scenario 는 경계에서 허용 목록 검증을 끝낸 값이고, FAKE_DATA=false 면 None 이다.
        """

        def compute() -> SnapshotResponse | None:
            return self._repository.get_snapshot(station_id, destination_id, deadline, scenario)

        key = (station_id, destination_id, deadline, scenario)
        snapshot = self._cache.get_or_set(key, compute, self._cache_ttl)
        if snapshot is None:
            raise NotFoundError()
        return snapshot

    def get_explanation(self, snapshot_id: UUID) -> ExplanationResponse:
        """스냅샷 설명. 뼈대 단계에서는 항상 고정 문구(llm_disabled). 스냅샷이 없으면 404."""
        snapshot = self._repository.find_snapshot(snapshot_id)
        if snapshot is None:
            raise NotFoundError()
        return ExplanationResponse(
            snapshot_id=snapshot.snapshot_id,
            computed_at=snapshot.computed_at,
            source="fallback",
            fallback_reason="llm_disabled",
            text=FALLBACK_EXPLANATION_TEXT,
        )
