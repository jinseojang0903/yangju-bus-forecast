"""설정과 고정 상수.

- 비밀값(GBIS·DB·LLM 키)은 `backend/.env` 또는 환경변수에서만 읽는다.
- CLAUDE.md '바꾸지 않는 규칙'의 값은 이 모듈의 상수만 참조한다.
  다른 곳에 숫자를 다시 쓰지 않는다. 값을 바꿔야 할 것 같으면 고치지 말고 메인에 보고한다.
"""

import math
from dataclasses import dataclass
from datetime import time
from functools import lru_cache
from pathlib import Path
from typing import Final
from urllib.parse import unquote
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# 경로. 현재 작업 폴더가 아니라 이 파일 위치를 기준으로 잡는다.
# ---------------------------------------------------------------------------
BACKEND_DIR: Final = Path(__file__).resolve().parents[2]
REPO_ROOT: Final = BACKEND_DIR.parent
ENV_FILE: Final = BACKEND_DIR / ".env"
DEFAULT_COLLECT_DATA_DIR: Final = REPO_ROOT / "data" / "collected"
DEFAULT_LOG_DIR: Final = REPO_ROOT / "data" / "logs"
GBIS_FIXTURE_DIR: Final = BACKEND_DIR / "tests" / "fixtures" / "gbis"

RAW_POLL_FILENAME: Final = "raw_poll.jsonl"
STATUS_FILENAME: Final = "status.json"
LOCK_FILENAME: Final = "collector.lock"
LOG_FILENAME: Final = "collector.log"
# 기준정보(discover): <데이터폴더>/reference/<YYYY-MM-DD>/ 에 노선 API 원본 기록과 targets.json.
# DB 의 route·station·route_station 은 다음 단계에서 이 파일로 채운다.
REFERENCE_DIRNAME: Final = "reference"
REFERENCE_RECORD_SUFFIX: Final = ".record.json"
TARGETS_FILENAME: Final = "targets.json"
LOG_MAX_BYTES: Final = 5 * 1024 * 1024
LOG_BACKUP_COUNT: Final = 10

# ---------------------------------------------------------------------------
# 시간대와 수집 창
# ---------------------------------------------------------------------------
TIMEZONE_NAME: Final = "Asia/Seoul"
KST: Final = ZoneInfo(TIMEZONE_NAME)


@dataclass(frozen=True)
class CollectWindow:
    """평일(월~금) start 이상 end 미만(KST)에 호출한다.

    대상별 주기는 TargetRoute.interval_sec·CollectTarget.arrival_interval_sec 에 있다.
    interval_sec 는 주기를 따로 정하지 않은 곳(기본 인자)의 기본 간격이다.
    """

    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # datetime.weekday(): 월=0 … 금=4
    # 05:30 이상 10:15 미만: 사용자 결정(2026-10-06), 2026-10-07 부터. (이전 05:00~10:00)
    start: time = time(5, 30)
    end: time = time(10, 15)
    interval_sec: int = 60


COLLECT_WINDOW: Final = CollectWindow()
# 잠에서 경계보다 늦게 깨어났을 때 그 경계의 호출로 인정하는 여유(초).
# 운영값이며 규칙 값이 아니다.
TICK_GRACE_SEC: Final = 5.0
# 수집 주기로 쓸 수 있는 범위(초). 시운전 --interval-sec 와 대상별 주기 모두 이 범위이고
# 86400 의 약수여야 한다(KST 자정 기준 경계가 매일 같게).
TRIAL_INTERVAL_MIN_SEC: Final = 10
TRIAL_INTERVAL_MAX_SEC: Final = 60


# ---------------------------------------------------------------------------
# 수집 대상
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TargetRoute:
    route_name: str  # 로그·상태 파일·--only-route 에 쓰는 이름. 목록 안에서 겹치지 않아야 한다
    # 잠실행 하차 정류장 이름. 라벨 대상(discover·라벨)만 쓴다.
    alight_station_name: str = ""
    # discover 결과·노선 API 확인값을 메인이 채운다. None 이면 once/run 은 호출하지 않고 끝낸다.
    route_id: str | None = None
    # 위치 API 호출 주기(초).
    interval_sec: int = 60
    collect: bool = True  # False 면 목록에만 두고 호출하지 않는다
    label_target: bool = False  # 운행편·라벨·DB 적재 대상(G1300·1306)
    is_reserved: bool = False  # 예약버스(P 노선)
    is_night: bool = False  # 심야 전용
    note: str = ""


@dataclass(frozen=True)
class CollectTarget:
    board_station_name: str
    direction_label: str
    destination_name: str
    # 라벨 대상 노선(덕현초교 잠실행 정류장 순번이 있는 노선). discover·라벨 모듈이 쓴다.
    routes: tuple[TargetRoute, ...]
    # 덕현초교(잠실행) stationId. discover 결과를 메인이 채운다.
    board_station_id: str | None = None
    # 도착 API 호출 주기(초).
    arrival_interval_sec: int = 60
    walk_minutes_allowed: int = 0
    transfers_allowed: int = 0
    # discover 가 노선 검색 결과 여럿 중 하나를 고를 때 지역 필드에서 찾는 말.
    # 기점·종점 쪽은 destination_name('잠실')으로 찾는다.
    region_keyword: str = "양주"
    # 위치 API 수집 노선 목록(collect=False 인 노선도 표시와 함께 둔다). 비어 있으면 routes 를 쓴다.
    route_catalog: tuple[TargetRoute, ...] = ()

    @property
    def collect_routes(self) -> tuple[TargetRoute, ...]:
        """위치 API 를 부르는 노선(목록 순서). route_id 가 없는 노선도 들어 있다."""
        return tuple(r for r in (self.route_catalog or self.routes) if r.collect)


# 2026-10-06 discover 결과(노선 API, data/collected/reference/2026-10-06/targets.json).
# 하차 정류장 ID(F04 에서 사용): G1300 잠실광역환승센터 123000611(순번 30, 회차 지점),
# 1306 잠실역.잠실대교남단(중) 123000002(순번 26, 회차 지점). 1306 의 '잠실역' 123000511 은
# 회차 뒤(순번 29, 돌아오는 방향)라 잠실행 하차로 쓰지 않는다.
ROUTE_G1300: Final = TargetRoute(
    route_name="G1300",
    alight_station_name="잠실광역환승센터",
    route_id="235000092",
    interval_sec=10,  # 사용자 결정(2026-10-06): G1300 위치 10초
    label_target=True,
)
ROUTE_1306: Final = TargetRoute(
    route_name="1306",
    alight_station_name="잠실역",
    route_id="235000123",
    interval_sec=30,  # 사용자 결정(2026-10-06): 1306 위치 30초
    label_target=True,
)

# 양주시 광역 노선 목록. 사용자 결정(2026-10-06), routeId 는 메인이 노선 API 로 확인했다.
# 관할 '경기도 양주시' 직행좌석형 11개를 수집한다(G1300 10초, 1306 30초, 나머지 40초).
# 원본(JSONL)은 전 노선을 남기고, 운행편·라벨·DB 적재는 label_target(G1300·1306)만 한다.
OTHER_ROUTE_INTERVAL_SEC: Final = 40  # 사용자 결정(2026-10-06): 나머지 노선 위치 40초
YANGJU_ROUTES: Final = (
    ROUTE_G1300,
    ROUTE_1306,
    TargetRoute(
        route_name="1100",
        route_id="235000085",
        interval_sec=OTHER_ROUTE_INTERVAL_SEC,
        note="덕정역→마들역. 남양주 1100(222000074)과 다른 노선",
    ),
    TargetRoute(route_name="1101", route_id="235000115", interval_sec=OTHER_ROUTE_INTERVAL_SEC),
    TargetRoute(route_name="1304", route_id="235000118", interval_sec=OTHER_ROUTE_INTERVAL_SEC),
    TargetRoute(route_name="1407", route_id="235000131", interval_sec=OTHER_ROUTE_INTERVAL_SEC),
    TargetRoute(route_name="8300", route_id="235000120", interval_sec=OTHER_ROUTE_INTERVAL_SEC),
    TargetRoute(route_name="8906", route_id="235000103", interval_sec=OTHER_ROUTE_INTERVAL_SEC),
    TargetRoute(route_name="G1200", route_id="235000104", interval_sec=OTHER_ROUTE_INTERVAL_SEC),
    TargetRoute(
        route_name="P9601(출근)",
        route_id="233000371",
        interval_sec=OTHER_ROUTE_INTERVAL_SEC,
        is_reserved=True,
    ),
    TargetRoute(
        route_name="P9602(출근)",
        route_id="233000373",
        interval_sec=OTHER_ROUTE_INTERVAL_SEC,
        is_reserved=True,
    ),
    TargetRoute(
        route_name="P9603(출근)",
        route_id="235000127",
        interval_sec=OTHER_ROUTE_INTERVAL_SEC,
        is_reserved=True,
    ),
    # 아래는 목록에만 둔다(collect=False).
    TargetRoute(route_name="G1300N", route_id="235000116", collect=False, is_night=True),
    TargetRoute(
        route_name="P9601(퇴근)",
        route_id="233000372",
        collect=False,
        is_reserved=True,
        note="퇴근 편. 아침 창에 운행하지 않는다",
    ),
    TargetRoute(
        route_name="P9602(퇴근)",
        route_id="233000374",
        collect=False,
        is_reserved=True,
        note="퇴근 편. 아침 창에 운행하지 않는다",
    ),
    TargetRoute(
        route_name="P9603(퇴근)",
        route_id="235000128",
        collect=False,
        is_reserved=True,
        note="퇴근 편. 아침 창에 운행하지 않는다",
    ),
    TargetRoute(
        route_name="3800",
        route_id="218000151",
        collect=False,
        note="양주를 지나는 타 시 관할 노선. 수집 여부 사용자 확인 대기",
    ),
    TargetRoute(
        route_name="8109",
        route_id="234001236",
        collect=False,
        note="양주를 지나는 타 시 관할 노선. 수집 여부 사용자 확인 대기",
    ),
)

COLLECT_TARGET: Final = CollectTarget(
    board_station_name="덕현초교",
    direction_label="잠실행",
    destination_name="잠실",
    routes=(ROUTE_G1300, ROUTE_1306),
    # 덕현초교.덕고개 잠실행(mobileNo 39624). G1300 순번 13, 1306 순번 11. 두 노선이 같은 ID 를
    # 쓰므로 도착 API 는 1곳만 부른다. 반대 방향은 235000409(mobileNo 39625, 회차 뒤).
    board_station_id="235000392",
    arrival_interval_sec=30,  # 사용자 결정(2026-10-06): 도착(덕현초교) 30초
    # 허용 보행시간 0분·환승 없음: 사용자 결정(2026-10-06), CLAUDE.md 표 밖의 값.
    walk_minutes_allowed=0,
    transfers_allowed=0,
    route_catalog=YANGJU_ROUTES,
)


# ---------------------------------------------------------------------------
# GBIS (공공데이터포털 경기도 v2).
# 아래 경로·목록 이름·결과 코드는 실호출로 메인이 검증한다.
# 틀린 것이 있으면 이 블록만 고친다.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class GbisEndpoint:
    api: str  # 오퍼레이션 이름. JSONL 의 api 값
    service: str  # 하루 호출 한도를 세는 단위(공공데이터포털 API)
    url: str
    list_key: str  # response.msgBody 안의 목록 이름


GBIS_BASE_URL: Final = "https://apis.data.go.kr/6410000"

GBIS_BUS_LOCATION: Final = GbisEndpoint(
    api="getBusLocationListv2",
    service="buslocationservice",
    url=f"{GBIS_BASE_URL}/buslocationservice/v2/getBusLocationListv2",
    list_key="busLocationList",
)
GBIS_BUS_ARRIVAL: Final = GbisEndpoint(
    api="getBusArrivalListv2",
    service="busarrivalservice",
    url=f"{GBIS_BASE_URL}/busarrivalservice/v2/getBusArrivalListv2",
    list_key="busArrivalList",
)
GBIS_ROUTE_LIST: Final = GbisEndpoint(
    api="getBusRouteListv2",
    service="busrouteservice",
    url=f"{GBIS_BASE_URL}/busrouteservice/v2/getBusRouteListv2",
    list_key="busRouteList",
)
# 노선 상세. 하루 4회 규칙 때문에 discover 에서는 부르지 않는다(경로 기록용으로만 둔다).
GBIS_ROUTE_INFO: Final = GbisEndpoint(
    api="getBusRouteInfoItemv2",
    service="busrouteservice",
    url=f"{GBIS_BASE_URL}/busrouteservice/v2/getBusRouteInfoItemv2",
    list_key="busRouteInfoItem",
)
GBIS_ROUTE_STATIONS: Final = GbisEndpoint(
    api="getBusRouteStationListv2",
    service="busrouteservice",
    url=f"{GBIS_BASE_URL}/busrouteservice/v2/getBusRouteStationListv2",
    list_key="busRouteStationList",
)
GBIS_ENDPOINTS: Final = (
    GBIS_BUS_LOCATION,
    GBIS_BUS_ARRIVAL,
    GBIS_ROUTE_LIST,
    GBIS_ROUTE_INFO,
    GBIS_ROUTE_STATIONS,
)

# msgHeader.resultCode. 숫자 문자열은 앞의 0 을 떼고 비교한다("00" → "0").
GBIS_SUCCESS_CODES: Final = frozenset({"0"})
# 차가 없을 때 등 "결과가 존재하지 않습니다". 실패가 아니다.
GBIS_NO_RESULT_CODES: Final = frozenset({"4"})
# format=json 이어도 게이트웨이 오류는 XML 로 온다.
GBIS_GATEWAY_ERROR_MARKERS: Final = ("OpenAPI_ServiceResponse", "returnAuthMsg", "cmmMsgHeader")
# 공공데이터포털 게이트웨이의 하루 호출량 초과 오류. 응답 원문에 이 말이 있거나
# returnReasonCode(또는 포털 표준 header.resultCode)가 22 이면 호출량 초과로 본다.
GBIS_QUOTA_EXCEEDED_MARKER: Final = "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
GBIS_QUOTA_EXCEEDED_REASON_CODES: Final = frozenset({"22"})
# 같은 API 에서 호출량 초과가 이만큼 연속되면, 그날 그 API 는 아래 간격으로만 시험 호출한다.
# 정상 응답이 오면 원래 주기로 돌아간다. 운영값이며 규칙 값이 아니다.
QUOTA_THROTTLE_AFTER_CONSECUTIVE: Final = 3
QUOTA_THROTTLE_PROBE_INTERVAL_SEC: Final = 300
# 수집 창 안에서 한 대상이 max(이 배수 × 주기, 최소 초) 넘게 호출을 시도하지 않으면
# 정체로 기록한다(워치독. 기록만 한다).
WORKER_STALL_INTERVALS: Final = 3
WORKER_STALL_MIN_SEC: Final = 60
GBIS_HTTP_TIMEOUT_SEC: Final = 10.0
# 호출 타임아웃은 그 대상의 주기보다 이만큼 짧게 한다(10초 주기 → 8초).
CALL_TIMEOUT_MARGIN_SEC: Final = 2.0


def call_timeout_sec(interval_sec: int) -> float:
    """대상 주기에 맞춘 HTTP 타임아웃(초): min(10초, 주기 − 2초), 최소 1초."""
    return max(1.0, min(GBIS_HTTP_TIMEOUT_SEC, interval_sec - CALL_TIMEOUT_MARGIN_SEC))


@dataclass(frozen=True)
class ApiQuota:
    daily_limit: int  # 공공데이터포털 하루 한도
    safe_limit: int  # 실제 호출 수가 여기에 닿으면 그날 그 API 의 모든 호출을 멈춘다
    planned_max: int  # 정식 수집 하루 예상 호출 상한. 넘는 설정이면 run 이 시작하지 않는다


@dataclass(frozen=True)
class CallLimits:
    # 위치 API: 이 키는 운영계정 하루 10,000회(사용자 확인 2026-10-06). 계획 상한 9,000회.
    location: ApiQuota = ApiQuota(daily_limit=10_000, safe_limit=9_800, planned_max=9_000)
    # 도착 API: 하루 1,000회(개발계정). 계획 상한 950회.
    arrival: ApiQuota = ApiQuota(daily_limit=1_000, safe_limit=980, planned_max=950)
    # 노선 API 는 기준정보(routeId·stationId·정류장 순서)를 찾을 때만 하루 4회 이하로 쓴다.
    # 사용자 규칙(2026-10-06). discover 1회 = 노선 2개 × (검색 1 + 정류장 목록 1) = 4회.
    route_daily_limit: int = 4
    route_service: str = GBIS_ROUTE_LIST.service
    route_api_daily_limit: int = 1_000  # 노선 API 포털 한도(개발계정)
    # 위 셋이 아닌 서비스 이름(예상하지 않은 값)에 쓰는 보수적인 값.
    other: ApiQuota = ApiQuota(daily_limit=1_000, safe_limit=980, planned_max=950)

    def quota_for(self, service: str) -> ApiQuota:
        if service == GBIS_BUS_LOCATION.service:
            return self.location
        if service == GBIS_BUS_ARRIVAL.service:
            return self.arrival
        if service == self.route_service:
            return ApiQuota(
                daily_limit=self.route_api_daily_limit,
                safe_limit=self.route_daily_limit,
                planned_max=self.route_daily_limit,
            )
        return self.other

    def safe_limit_for(self, service: str) -> int:
        return self.quota_for(service).safe_limit

    def planned_max_for(self, service: str) -> int:
        return self.quota_for(service).planned_max

    def as_dict(self) -> dict[str, dict[str, int]]:
        """상태 파일·status 출력용. 서비스 → {daily_limit, safe_limit, planned_max}."""
        services = (GBIS_BUS_LOCATION.service, GBIS_BUS_ARRIVAL.service, self.route_service)
        return {
            s: {
                "daily_limit": self.quota_for(s).daily_limit,
                "safe_limit": self.quota_for(s).safe_limit,
                "planned_max": self.quota_for(s).planned_max,
            }
            for s in services
        }


CALL_LIMITS: Final = CallLimits()


def _seconds_of_day(moment: time) -> int:
    return moment.hour * 3600 + moment.minute * 60 + moment.second


def _boundaries_in_window(interval_sec: int, window: CollectWindow) -> int:
    """창 [start, end) 안에 있는 KST 자정 기준 interval_sec 배수 경계의 수."""
    start, end = _seconds_of_day(window.start), _seconds_of_day(window.end)
    return math.ceil(end / interval_sec) - math.ceil(start / interval_sec)


def target_intervals(target: CollectTarget = COLLECT_TARGET) -> dict[str, int]:
    """대상별 정식 주기(초). 키는 'location:<노선>', 'arrival:<정류장>'. ID 없는 대상은 뺀다."""
    intervals = {
        f"location:{r.route_name}": r.interval_sec for r in target.collect_routes if r.route_id
    }
    if target.board_station_id:
        intervals[f"arrival:{target.board_station_name}"] = target.arrival_interval_sec
    return intervals


def planned_daily_calls(
    target: CollectTarget = COLLECT_TARGET, window: CollectWindow = COLLECT_WINDOW
) -> dict[str, int]:
    """정식 수집의 API(서비스)별 하루 예상 호출 수(창 시작 포함·끝 제외 경계 수의 합).

    지금 설정(05:30~10:15 = 17,100초)이면 buslocationservice
    G1300 1,710 + 1306 570 + 40초 노선 10개 × 428 = 6,560, busarrivalservice 570.
    ID 가 없는 대상은 호출하지 않으므로 세지 않는다.
    """
    planned: dict[str, int] = {}
    for route in target.collect_routes:
        if route.route_id:
            service = GBIS_BUS_LOCATION.service
            planned[service] = planned.get(service, 0) + _boundaries_in_window(
                route.interval_sec, window
            )
    if target.board_station_id:
        service = GBIS_BUS_ARRIVAL.service
        planned[service] = planned.get(service, 0) + _boundaries_in_window(
            target.arrival_interval_sec, window
        )
    return planned


# DB(raw_poll) 저장. 테이블 SQL 이 아직 없어 끈다.
# TODO(backend-dev/2026-10-06): raw_poll 테이블 SQL(backend/migrations)과
#   insert 를 구현한 뒤 켠다.
RAW_POLL_DB_SAVE_ENABLED: Final = False


# ---------------------------------------------------------------------------
# '바꾸지 않는 규칙' (기획안 사전 고정값). 로직은 아직 없다. 값만 둔다.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class ForecastRules:
    lead_times_min: tuple[int, ...] = (5, 10, 15)
    seat_tolerance: int = 2  # 잔여석 ±2석
    headway_tolerance_min: int = 3  # 앞차 간격 ±3분
    seat_distance_divisor: int = 2  # |잔여석 차| ÷ 2
    headway_distance_divisor: int = 3  # |간격 차| ÷ 3
    max_cases: int = 30
    min_cases_to_show: int = 20  # 이 미만이면 확률을 내지 않는다
    risk_high_min: float = 0.7  # 이상이면 높음
    risk_low_below: float = 0.3  # 미만이면 낮음


@dataclass(frozen=True)
class ArrivalRules:
    travel_time_percentile: int = 90
    travel_time_recent_weekdays: int = 10
    travel_time_min_records: int = 10  # 미만이면 '도착시각 미제공'
    time_bin_minutes: int = 30


@dataclass(frozen=True)
class PublishCriteria:
    field_match_min: float = 0.90
    calibration_error_max_pp: float = 10.0
    alert_probability_min: float = 0.7
    alert_recall_min: float = 0.80
    alert_precision_min: float = 0.80


@dataclass(frozen=True)
class LlmLimits:
    timeout_sec: float = 3.0
    daily_call_limit: int = 300


FORECAST_RULES: Final = ForecastRules()
ARRIVAL_RULES: Final = ArrivalRules()
PUBLISH_CRITERIA: Final = PublishCriteria()
LLM_LIMITS: Final = LlmLimits()


# ---------------------------------------------------------------------------
# 환경변수
# ---------------------------------------------------------------------------
class Settings(BaseSettings):
    """`backend/.env` 와 환경변수에서 읽는다. 환경변수가 .env 보다 우선한다."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gbis_service_key: SecretStr = SecretStr("")
    database_url: SecretStr = SecretStr("")
    llm_api_key: SecretStr = SecretStr("")
    collect_data_dir: str = ""

    @property
    def service_key(self) -> str:
        """GBIS 서비스 키. Encoding 값(% 포함)이면 한 번 디코딩한다. 없으면 빈 문자열."""
        raw = self.gbis_service_key.get_secret_value().strip()
        return unquote(raw) if "%" in raw else raw

    @property
    def has_service_key(self) -> bool:
        return bool(self.service_key)

    @property
    def has_database_url(self) -> bool:
        return bool(self.database_url.get_secret_value().strip())

    @property
    def data_dir(self) -> Path:
        configured = self.collect_data_dir.strip()
        return Path(configured).expanduser() if configured else DEFAULT_COLLECT_DATA_DIR

    @property
    def log_dir(self) -> Path:
        # COLLECT_DATA_DIR 를 OneDrive 밖으로 옮겼으면 로그도 같이 옮겨 동기화 충돌을 피한다.
        configured = self.collect_data_dir.strip()
        return Path(configured).expanduser() / "logs" if configured else DEFAULT_LOG_DIR

    def secret_values(self) -> list[str]:
        """로그·파일에서 가릴 값. 서비스 키는 입력 원문과 디코딩 값을 모두 넣는다."""
        values = [
            self.gbis_service_key.get_secret_value().strip(),
            self.service_key,
            self.database_url.get_secret_value().strip(),
            self.llm_api_key.get_secret_value().strip(),
        ]
        return [v for v in values if v]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
