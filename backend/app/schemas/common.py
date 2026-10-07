"""공통 타입·ID 정규식·상태값(계약 2장·5장). 이 파일에만 정의하고 다른 곳은 가져다 쓴다."""

from typing import Literal

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from app.core.settings import FORECAST_RULES

# ---------------------------------------------------------------------------
# ID·시각 형식(계약 2장). 형식이 틀리면 400, 형식은 맞지만 없으면 404.
# ---------------------------------------------------------------------------
STATION_ID_PATTERN = r"^[0-9]{1,20}$"
ROUTE_ID_PATTERN = r"^[0-9]{1,20}$"
DESTINATION_ID_PATTERN = r"^[a-z_]{1,32}$"
TIME_OF_DAY_PATTERN = r"^([01][0-9]|2[0-3]):[0-5][0-9]$"
QUERY_TEXT_MAX_LENGTH = 200

# ---------------------------------------------------------------------------
# 상태값과 열거형(계약 2장·4장·5장)
# ---------------------------------------------------------------------------
# 선행시간 값은 settings.FORECAST_RULES 한 곳에만 둔다.
# 실행 시 Literal[(5, 10, 15)] 은 Literal[5, 10, 15] 와 같다.
LeadTimeMin = Literal[FORECAST_RULES.lead_times_min]  # type: ignore[valid-type]
RiskLevel = Literal["high", "medium", "low"]
ForecastStatus = Literal[
    "ok",
    "not_yet",
    "insufficient_cases",
    "not_validated",
    "stale",
    "missing_input",
    "outside_hours",
]
AlternativeStatus = Literal["recommended", "no_alternative", "arrival_unavailable", "undecidable"]
ReasonCode = Literal["lowest_risk", "earliest_among_equal_risk"]
ServiceState = Literal["in_service", "outside_collection", "outside_forecast_hours"]
BusSource = Literal["arrival_1st", "arrival_2nd", "timetable_next"]
ArrivalEstimateSource = Literal["predict_time_sec", "predict_time_min", "timetable"]
FallbackReason = Literal["llm_disabled", "timeout", "check_failed", "daily_limit", "error"]
HealthStatus = Literal["ok", "degraded", "idle"]
ErrorCode = Literal[
    "VALIDATION_FAILED", "NOT_FOUND", "METHOD_NOT_ALLOWED", "RATE_LIMITED", "INTERNAL_ERROR"
]


class ApiModel(BaseModel):
    """응답·요청 모델의 공통 설정: 파이썬 이름은 snake_case, JSON 은 camelCase."""

    model_config = ConfigDict(
        alias_generator=to_camel,
        validate_by_name=True,
        validate_by_alias=True,
        serialize_by_alias=True,
    )
