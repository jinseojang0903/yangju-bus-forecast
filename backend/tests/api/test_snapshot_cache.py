"""스냅샷 캐시: min(TTL, nextRefreshAt − 지금), 없음(None)은 캐시하지 않음."""

from datetime import timedelta

import pytest

from app.core.errors import NotFoundError
from app.repositories.fake import FakeRepository
from app.schemas.snapshot import SnapshotResponse
from app.services.cache import TtlCache
from app.services.snapshot import SnapshotService
from tests.api.helpers import DESTINATION, FIXED_NOW, STATION, FakeClock


class CountingRepository(FakeRepository):
    def __init__(self) -> None:
        super().__init__()
        self.calls = 0

    def get_snapshot(
        self,
        station_id: str,
        destination_id: str,
        deadline: str | None,
        scenario: str | None,
    ) -> SnapshotResponse | None:
        self.calls += 1
        return super().get_snapshot(station_id, destination_id, deadline, scenario)


def make_service(
    now_offset_sec: float = 0,
) -> tuple[SnapshotService, CountingRepository, FakeClock]:
    clock = FakeClock()
    repository = CountingRepository()
    cache: TtlCache[SnapshotResponse | None] = TtlCache(ttl_sec=10, clock=clock)
    now = FIXED_NOW + timedelta(seconds=now_offset_sec)
    return SnapshotService(repository, cache, now), repository, clock


def test_cache_reuses_within_ttl_and_expires_after() -> None:
    service, repository, clock = make_service()

    service.get_snapshot(STATION, DESTINATION, None, None)
    service.get_snapshot(STATION, DESTINATION, None, None)
    assert repository.calls == 1

    clock.advance(10)
    service.get_snapshot(STATION, DESTINATION, None, None)
    assert repository.calls == 2


def test_cache_expires_at_next_refresh_when_sooner() -> None:
    # 예시 nextRefreshAt 은 07:31:33. 지금이 07:31:30 이면 3초만 캐시한다.
    service, repository, clock = make_service(now_offset_sec=10)

    service.get_snapshot(STATION, DESTINATION, None, None)
    clock.advance(2)
    service.get_snapshot(STATION, DESTINATION, None, None)
    assert repository.calls == 1

    clock.advance(1)
    service.get_snapshot(STATION, DESTINATION, None, None)
    assert repository.calls == 2


def test_cache_skips_when_next_refresh_already_passed() -> None:
    service, repository, _ = make_service(now_offset_sec=60)

    service.get_snapshot(STATION, DESTINATION, None, None)
    service.get_snapshot(STATION, DESTINATION, None, None)

    assert repository.calls == 2


def test_not_found_is_not_cached() -> None:
    service, repository, _ = make_service()

    for _ in range(2):
        with pytest.raises(NotFoundError):
            service.get_snapshot("999999999", DESTINATION, None, None)

    assert repository.calls == 2
