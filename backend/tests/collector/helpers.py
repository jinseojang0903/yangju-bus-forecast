"""수집기 테스트 공용 도구. 네트워크를 쓰지 않는다(httpx.MockTransport)."""

from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import quote, quote_plus

import httpx

from app.collector.collector import Collector
from app.collector.db import RawPollSink
from app.collector.gbis import GbisClient
from app.collector.redact import Redactor
from app.core.settings import (
    CALL_LIMITS,
    KST,
    CallLimits,
    CollectTarget,
    Settings,
    TargetRoute,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "gbis"

# 가짜 서비스 키(의미 없는 값).
# '+', '/', '=' 를 넣어 Decoding 값과 Encoding 값이 서로 다르게 했다.
FAKE_KEY = "TestOnlyFakeKey+NotReal/0123456789abcdefABCDEF+xyz=="
FAKE_KEY_ENCODED = quote(FAKE_KEY, safe="")
FAKE_KEY_FORMS = (FAKE_KEY, FAKE_KEY_ENCODED, quote_plus(FAKE_KEY, safe=""))

TEST_TARGET = CollectTarget(
    board_station_name="덕현초교",
    direction_label="잠실행",
    destination_name="잠실",
    routes=(
        TargetRoute(
            route_name="G1300", alight_station_name="잠실광역환승센터", route_id="900000001"
        ),
        TargetRoute(route_name="1306", alight_station_name="잠실역", route_id="900000002"),
    ),
    board_station_id="900000105",
)
# 정식 설정과 같은 주기(G1300 30초, 1306·도착 60초)의 테스트 대상.
MIXED_TARGET = CollectTarget(
    board_station_name="덕현초교",
    direction_label="잠실행",
    destination_name="잠실",
    routes=(
        TargetRoute(
            route_name="G1300",
            alight_station_name="잠실광역환승센터",
            route_id="900000001",
            interval_sec=30,
        ),
        TargetRoute(
            route_name="1306", alight_station_name="잠실역", route_id="900000002", interval_sec=60
        ),
    ),
    board_station_id="900000105",
    arrival_interval_sec=60,
)

Handler = Callable[[httpx.Request], httpx.Response]


def kst(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class FakeClock:
    def __init__(self, start: datetime) -> None:
        self.current = start

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)


class OvershootClock(FakeClock):
    """실제 OS 처럼 sleep 이 요청보다 3ms 늦게 깨어난다."""

    def sleep(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds, milliseconds=3)


class RecordingHandler:
    """경로로 합성 픽스처를 골라 돌려주고, 받은 요청을 남긴다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if request.url.path.endswith("getBusLocationListv2"):
            return httpx.Response(200, text=load_fixture("bus_location_ok_synthetic.json"))
        if request.url.path.endswith("getBusArrivalListv2"):
            return httpx.Response(200, text=load_fixture("bus_arrival_ok_synthetic.json"))
        return httpx.Response(404, text="not found")

    def count(self, api: str) -> int:
        return sum(1 for r in self.requests if r.url.path.endswith(api))

    def count_route(self, route_id: str) -> int:
        return sum(1 for r in self.requests if r.url.params.get("routeId") == route_id)


def make_settings(key: str = FAKE_KEY, **overrides: str) -> Settings:
    # backend/.env 를 읽지 않는다.
    return Settings(_env_file=None, gbis_service_key=key, **overrides)


def make_client(handler: Handler, settings: Settings, redactor: Redactor) -> GbisClient:
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return GbisClient(settings.service_key, redactor, http=http)


def make_collector(
    data_dir: Path,
    clock: FakeClock,
    handler: Handler,
    *,
    key: str = FAKE_KEY,
    limits: CallLimits = CALL_LIMITS,
    mode: str = "run",
    sleep: Callable[[float], object] | None = None,
    db_sink: RawPollSink | None = None,
    interval_sec: int | None = None,
    target: CollectTarget = TEST_TARGET,
) -> Collector:
    settings = make_settings(key)
    redactor = Redactor(settings.secret_values())
    return Collector(
        data_dir=data_dir,
        client=make_client(handler, settings, redactor),
        clock=clock,
        redactor=redactor,
        mode=mode,
        target=target,
        limits=limits,
        db_sink=db_sink,
        sleep=sleep or clock.sleep,
        max_sleep_chunk_sec=60.0,
        interval_sec=interval_sec,
    )


def read_all_text(root: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file()
    )
