"""예보 저장소의 모양. 가짜 저장소와 이후 DB 저장소가 같은 메서드를 갖는다.

TODO(main/2026-10-06): DB 저장소 도입 시 규칙 계산을 services 로 올린다.
지금은 저장소가 계산이 끝난 SnapshotResponse(API 모델)를 돌려준다.
"""

from typing import Protocol
from uuid import UUID

from app.schemas.snapshot import SnapshotResponse
from app.schemas.stations import StationItem, StationRoutesResponse


class ForecastRepository(Protocol):
    def list_stations(self) -> list[StationItem]:
        """출발 정류장 목록."""
        ...

    def get_station_routes(self, station_id: str) -> StationRoutesResponse | None:
        """정류장의 목적지·노선. 정류장이 없으면 None."""
        ...

    def get_snapshot(
        self,
        station_id: str,
        destination_id: str,
        deadline: str | None,
        scenario: str | None,
    ) -> SnapshotResponse | None:
        """(정류장, 목적지, 마감)의 지금 스냅샷. 정류장·목적지가 없으면 None.

        scenario 는 가짜 저장소에서만 쓴다(검증은 경계에서 끝난 값). DB 저장소는 무시한다.
        """
        ...

    def find_snapshot(self, snapshot_id: UUID) -> SnapshotResponse | None:
        """저장된 스냅샷. 없거나 만료됐으면 None."""
        ...
