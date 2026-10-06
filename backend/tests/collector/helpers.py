"""수집기 테스트 공용 도구. 네트워크를 쓰지 않는다(httpx.MockTransport)."""

import threading
from collections.abc import Callable, Sequence
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
    ApiQuota,
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
            route_name="G1300",
            alight_station_name="잠실광역환승센터",
            route_id="900000001",
            label_target=True,
        ),
        TargetRoute(
            route_name="1306", alight_station_name="잠실역", route_id="900000002", label_target=True
        ),
    ),
    board_station_id="900000105",
)
# G1300 30초, 1306·도착 60초.
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
            label_target=True,
        ),
        TargetRoute(
            route_name="1306",
            alight_station_name="잠실역",
            route_id="900000002",
            interval_sec=60,
            label_target=True,
        ),
    ),
    board_station_id="900000105",
    arrival_interval_sec=60,
)
# 정식 설정과 같은 모양: G1300 10초, 1306 30초, 그 밖 노선 40초, 도착 30초, 수집 안 하는 노선 1개.
G1300_ID, R1306_ID, R1100_ID, NIGHT_ID = "900000001", "900000002", "900000003", "900000009"
MULTI_LABEL_ROUTES = (
    TargetRoute(
        route_name="G1300",
        alight_station_name="잠실광역환승센터",
        route_id=G1300_ID,
        interval_sec=10,
        label_target=True,
    ),
    TargetRoute(
        route_name="1306",
        alight_station_name="잠실역",
        route_id=R1306_ID,
        interval_sec=30,
        label_target=True,
    ),
)
MULTI_TARGET = CollectTarget(
    board_station_name="덕현초교",
    direction_label="잠실행",
    destination_name="잠실",
    routes=MULTI_LABEL_ROUTES,
    board_station_id="900000105",
    arrival_interval_sec=30,
    route_catalog=(
        *MULTI_LABEL_ROUTES,
        TargetRoute(route_name="1100", route_id=R1100_ID, interval_sec=40),
        TargetRoute(route_name="G1300N", route_id=NIGHT_ID, collect=False, is_night=True),
    ),
)

# 작은 한도: 위치·도착 모두 안전 상한 3회.
SMALL_QUOTA = ApiQuota(daily_limit=5, safe_limit=3, planned_max=3)
SMALL_LIMITS = CallLimits(location=SMALL_QUOTA, arrival=SMALL_QUOTA)

Handler = Callable[[httpx.Request], httpx.Response]


def kst(
    year: int, month: int, day: int, hour: int = 0, minute: int = 0, second: int = 0
) -> datetime:
    return datetime(year, month, day, hour, minute, second, tzinfo=KST)


def load_fixture(name: str) -> str:
    return (FIXTURE_DIR / name).read_text(encoding="utf-8")


class FakeClock:
    """가상 시계이자 작업자 실행기(Collector 의 runner).

    run_all 로 띄운 작업자 스레드는 한 번에 하나만 돈다. 도는 스레드가 sleep 하면
    가장 이른 깨어날 시각(같으면 먼저 등록한 작업자)의 스레드를 그 시각으로 깨운다.
    그래서 스레드를 써도 결과가 매번 같다. 작업자 밖(테스트 본문)의 sleep 은 시각만 넘긴다.
    """

    overshoot = timedelta(0)
    join_timeout_sec = 60.0

    def __init__(self, start: datetime) -> None:
        self.current = start
        self._cond = threading.Condition()
        self._waiting: dict[int, datetime] = {}  # 작업자 번호 → 깨어날 시각
        self._token_by_thread: dict[int, int] = {}
        self._running: int | None = None
        self._is_held = False
        self._next_token = 0
        self.errors: list[Exception] = []
        self.started_jobs: list[str] = []
        self.finished_jobs: list[str] = []
        self.on_poll: Callable[[], object] | None = None

    def now(self) -> datetime:
        return self.current

    def sleep(self, seconds: float) -> None:
        delta = timedelta(seconds=seconds) + self.overshoot
        with self._cond:
            token = self._token_by_thread.get(threading.get_ident())
            if token is None:
                self.current += delta
                return
            self._waiting[token] = self.current + delta
            self._running = None
            self._schedule_locked()
            while self._running != token:
                self._cond.wait()

    def _schedule_locked(self) -> None:
        if self._running is not None or self._is_held or not self._waiting:
            return
        token = min(self._waiting, key=lambda t: (self._waiting[t], t))
        wake_at = self._waiting.pop(token)
        self.current = max(self.current, wake_at)
        self._running = token
        self._cond.notify_all()

    def run_all(
        self,
        jobs: Sequence[tuple[str, Callable[[], None]]],
        on_poll: Callable[[], object] | None = None,
    ) -> None:
        """on_poll(워치독)은 가상 시간에서 부를 때를 정할 수 없어 부르지 않는다.

        워치독은 테스트가 Collector.check_stalled 를 직접 불러 확인한다.
        """
        self.on_poll = on_poll
        threads: list[threading.Thread] = []
        with self._cond:
            self._is_held = True  # 모두 등록할 때까지 아무도 돌지 않는다
            for name, fn in jobs:
                token = self._next_token
                self._next_token += 1
                self._waiting[token] = self.current
                thread = threading.Thread(
                    target=self._thread_main, args=(token, name, fn), name=name, daemon=True
                )
                threads.append(thread)
                self.started_jobs.append(name)
            for thread in threads:
                thread.start()
            self._is_held = False
            self._schedule_locked()
        for thread in threads:
            thread.join(self.join_timeout_sec)
            if thread.is_alive():
                raise AssertionError(f"작업자가 끝나지 않았다: {thread.name}")
        if self.errors:
            raise self.errors[0]

    def _thread_main(self, token: int, name: str, fn: Callable[[], None]) -> None:
        ident = threading.get_ident()
        with self._cond:
            self._token_by_thread[ident] = token
            while self._running != token:
                self._cond.wait()
        try:
            fn()
        except Exception as exc:  # run_all 이 테스트 스레드에서 다시 올린다
            self.errors.append(exc)
        finally:
            with self._cond:
                del self._token_by_thread[ident]
                self.finished_jobs.append(name)
                self._running = None
                self._schedule_locked()

    @property
    def alive_jobs(self) -> int:
        return len(self.started_jobs) - len(self.finished_jobs)


class OvershootClock(FakeClock):
    """실제 OS 처럼 sleep 이 요청보다 3ms 늦게 깨어난다."""

    overshoot = timedelta(milliseconds=3)


class RecordingHandler:
    """경로로 합성 픽스처를 골라 돌려주고, 받은 요청을 남긴다."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self._lock = threading.Lock()

    def __call__(self, request: httpx.Request) -> httpx.Response:
        with self._lock:
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
    timeouts: list[float] | None = None,
) -> Collector:
    """작업자는 clock(FakeClock)이 가상 시간으로 돌린다. timeouts 에 대상별 타임아웃을 모은다."""
    settings = make_settings(key)
    redactor = Redactor(settings.secret_values())

    def client_factory(timeout_sec: float) -> GbisClient:
        if timeouts is not None:
            timeouts.append(timeout_sec)
        return make_client(handler, settings, redactor)

    return Collector(
        data_dir=data_dir,
        client_factory=client_factory,
        clock=clock,
        redactor=redactor,
        mode=mode,
        target=target,
        limits=limits,
        db_sink=db_sink,
        sleep=sleep or clock.sleep,
        max_sleep_chunk_sec=60.0,
        interval_sec=interval_sec,
        runner=clock,
    )


def read_all_text(root: Path) -> str:
    return "\n".join(
        p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file()
    )
