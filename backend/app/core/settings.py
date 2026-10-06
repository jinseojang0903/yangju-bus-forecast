"""설정과 고정 상수.

- 비밀값(GBIS·DB·LLM 키)은 `backend/.env` 또는 환경변수에서만 읽는다.
- CLAUDE.md '바꾸지 않는 규칙'의 값은 이 모듈의 상수만 참조한다.
  다른 곳에 숫자를 다시 쓰지 않는다. 값을 바꿔야 할 것 같으면 고치지 말고 메인에 보고한다.
"""

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
LOG_MAX_BYTES: Final = 5 * 1024 * 1024
LOG_BACKUP_COUNT: Final = 10

# ---------------------------------------------------------------------------
# 시간대와 수집 창
# ---------------------------------------------------------------------------
TIMEZONE_NAME: Final = "Asia/Seoul"
KST: Final = ZoneInfo(TIMEZONE_NAME)


@dataclass(frozen=True)
class CollectWindow:
    """평일(월~금) start 이상 end 미만(KST)에 interval_sec 마다 호출한다."""

    weekdays: tuple[int, ...] = (0, 1, 2, 3, 4)  # datetime.weekday(): 월=0 … 금=4
    start: time = time(5, 0)
    end: time = time(10, 0)
    interval_sec: int = 60


COLLECT_WINDOW: Final = CollectWindow()
# 잠에서 경계보다 늦게 깨어났을 때 그 경계의 호출로 인정하는 여유(초).
# 운영값이며 규칙 값이 아니다.
TICK_GRACE_SEC: Final = 5.0


# ---------------------------------------------------------------------------
# 수집 대상
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class TargetRoute:
    route_name: str
    alight_station_name: str
    # discover 결과를 메인이 채운다. None 이면 once/run 은 호출하지 않고 끝낸다.
    route_id: str | None = None


@dataclass(frozen=True)
class CollectTarget:
    board_station_name: str
    direction_label: str
    destination_name: str
    routes: tuple[TargetRoute, ...]
    # 덕현초교(잠실행) stationId. discover 결과를 메인이 채운다.
    board_station_id: str | None = None
    walk_minutes_allowed: int = 0
    transfers_allowed: int = 0


COLLECT_TARGET: Final = CollectTarget(
    board_station_name="덕현초교",
    direction_label="잠실행",
    destination_name="잠실",
    routes=(
        # route_id: discover 결과를 메인이 채운다.
        TargetRoute(route_name="G1300", alight_station_name="잠실광역환승센터", route_id=None),
        TargetRoute(route_name="1306", alight_station_name="잠실역", route_id=None),
    ),
    board_station_id=None,  # discover 결과를 메인이 채운다.
    # 허용 보행시간 0분·환승 없음: 사용자 결정(2026-10-06), CLAUDE.md 표 밖의 값.
    walk_minutes_allowed=0,
    transfers_allowed=0,
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
GBIS_HTTP_TIMEOUT_SEC: Final = 10.0


@dataclass(frozen=True)
class CallLimits:
    daily_limit_per_api: int = 1000  # 개발계정 한도
    daily_safe_limit_per_api: int = 980  # 여기에 닿으면 그 API 호출을 멈춘다


CALL_LIMITS: Final = CallLimits()

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
