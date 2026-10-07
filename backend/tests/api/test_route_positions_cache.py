"""노선 지도 캐시: min(TTL, nextRefreshAt − 지금), 노선별 키, 기준정보 없음 비캐시, 동시 요청."""

import threading
import time
from collections import Counter
from datetime import date, datetime
from pathlib import Path

import pytest

from app.core.errors import NotFoundError
from app.core.settings import KST
from app.repositories.route_positions import (
    LatestPositions,
    RoutePositionRepository,
    RouteStation,
    RouteStations,
)
from app.schemas.route_positions import RoutePositionsResponse
from app.services.cache import TtlCache
from app.services.route_positions import RoutePositionService
from tests.api.helpers import FIXED_NOW, STATION, FakeClock

G1300 = "235000092"
ROUTE_1306 = "235000123"
STATIONS = RouteStations(
    turn_seq=2,
    stations=(
        RouteStation(station_seq=1, station_id="100000001", name="기점", lat=37.85, lng=127.06),
        RouteStation(station_seq=2, station_id=STATION, name="덕현초교", lat=37.79, lng=127.08),
    ),
)


class CountingRepository(RoutePositionRepository):
    """파일 대신 고정 값을 돌려주고 노선별 계산 횟수를 센다."""

    def __init__(self, stations: RouteStations | None = STATIONS) -> None:
        super().__init__(Path("unused"))
        self.stations = stations
        self.calls: Counter[str] = Counter()
        self.release: threading.Event | None = None
        self.entered = threading.Event()

    def load_route_stations(self, route_id: str) -> RouteStations | None:
        self.calls[route_id] += 1
        self.entered.set()
        if self.release is not None:
            assert self.release.wait(5)
        return self.stations

    def find_latest_positions(
        self, route_id: str, today: date, *, lookback_days: int, include_trial: bool
    ) -> LatestPositions | None:
        return None


def make_cache(clock: FakeClock) -> TtlCache[RoutePositionsResponse | None]:
    return TtlCache(ttl_sec=10, clock=clock)


def service(
    repository: CountingRepository,
    cache: TtlCache[RoutePositionsResponse | None],
    now: datetime = FIXED_NOW,
) -> RoutePositionService:
    return RoutePositionService(repository, cache, now)


def test_cached_within_ttl_and_recomputed_after() -> None:
    clock = FakeClock()
    cache = make_cache(clock)
    repository = CountingRepository()

    first = service(repository, cache).get_positions(G1300)
    clock.advance(9)
    assert service(repository, cache).get_positions(G1300) is first
    assert repository.calls[G1300] == 1

    clock.advance(1)  # 07:31:20 의 nextRefreshAt 은 07:31:33(13초 뒤) → 기본 TTL 10초
    service(repository, cache).get_positions(G1300)
    assert repository.calls[G1300] == 2


def test_ttl_shortened_to_next_refresh_at() -> None:
    clock = FakeClock()
    cache = make_cache(clock)
    repository = CountingRepository()
    now = datetime(2026, 10, 7, 7, 31, 38, tzinfo=KST)  # nextRefreshAt 07:31:43 → 5초

    response = service(repository, cache, now).get_positions(G1300)
    assert response.next_refresh_at == datetime(2026, 10, 7, 7, 31, 43, tzinfo=KST)

    clock.advance(4.9)
    service(repository, cache, now).get_positions(G1300)
    assert repository.calls[G1300] == 1
    clock.advance(0.1)
    service(repository, cache, now).get_positions(G1300)
    assert repository.calls[G1300] == 2


def test_routes_are_cached_separately() -> None:
    cache = make_cache(FakeClock())
    repository = CountingRepository()

    g1300 = service(repository, cache).get_positions(G1300)
    r1306 = service(repository, cache).get_positions(ROUTE_1306)
    service(repository, cache).get_positions(G1300)
    service(repository, cache).get_positions(ROUTE_1306)

    assert (g1300.route_name, r1306.route_name) == ("G1300", "1306")
    assert repository.calls == Counter({G1300: 1, ROUTE_1306: 1})


def test_missing_reference_is_not_cached() -> None:
    cache = make_cache(FakeClock())
    repository = CountingRepository(stations=None)

    for _ in range(2):
        with pytest.raises(NotFoundError):
            service(repository, cache).get_positions(G1300)

    assert repository.calls[G1300] == 2


def test_unsupported_route_does_not_touch_repository() -> None:
    repository = CountingRepository()

    with pytest.raises(NotFoundError):
        service(repository, make_cache(FakeClock())).get_positions("235000085")

    assert repository.calls == Counter()


def test_concurrent_requests_compute_once() -> None:
    cache = make_cache(FakeClock())
    repository = CountingRepository()
    repository.release = threading.Event()
    results: list[RoutePositionsResponse] = []
    lock = threading.Lock()

    def request() -> None:
        response = service(repository, cache).get_positions(G1300)
        with lock:
            results.append(response)

    threads = [threading.Thread(target=request) for _ in range(5)]
    for thread in threads:
        thread.start()
    assert repository.entered.wait(5)
    time.sleep(0.2)  # 나머지 요청이 같은 키를 기다리게 둔다
    repository.release.set()
    for thread in threads:
        thread.join(5)

    assert repository.calls[G1300] == 1
    assert len(results) == 5
    assert all(result is results[0] for result in results)
