"""GET /snapshot, GET /snapshot/{snapshotId}/explanation 응답(계약 4.4·4.5절, 12장)."""

from datetime import datetime
from typing import Literal, get_args
from uuid import UUID

from pydantic import Field

from app.schemas.common import (
    AlternativeStatus,
    ApiModel,
    ArrivalEstimateSource,
    BusSource,
    FallbackReason,
    ForecastStatus,
    LeadTimeMin,
    ReasonCode,
    RiskLevel,
    ServiceState,
)

# 개발·시연용 scenario(계약 12장). FAKE_DATA=true 일 때만 받는다.
SnapshotScenario = Literal[
    "example",
    "status_ok",
    "status_not_yet",
    "status_insufficient_cases",
    "status_not_validated",
    "status_stale",
    "status_missing_input",
    "status_outside_hours",
    "alt_recommended",
    "alt_no_alternative",
    "alt_arrival_unavailable",
    "alt_undecidable",
    "service_outside_collection",
    "service_outside_forecast_hours",
    "published",
]
SNAPSHOT_SCENARIOS: tuple[str, ...] = get_args(SnapshotScenario)
DEFAULT_SCENARIO: SnapshotScenario = "example"


class ServiceInfo(ApiModel):
    state: ServiceState
    message: str | None
    next_forecast_start_at: datetime | None


class SnapshotStation(ApiModel):
    station_id: str
    name: str
    direction_label: str


class SnapshotDestination(ApiModel):
    destination_id: str
    name: str


class ForecastInputs(ApiModel):
    seats: int | None
    headway_min: float | None


class Forecast(ApiModel):
    lead_time_min: LeadTimeMin
    status: ForecastStatus
    issued_at: datetime | None
    no_seat_probability: float | None = Field(ge=0, le=1)
    n: int | None
    k: int | None
    risk_level: RiskLevel | None
    preliminary: bool
    inputs: ForecastInputs


class Bus(ApiModel):
    route_id: str
    route_name: str
    vehicle_id: str | None
    plate_no: str | None
    source: BusSource
    station_arrival_at: datetime | None
    arrival_estimate_source: ArrivalEstimateSource | None
    minutes_to_arrival: int | None
    in_forecast_hours: bool
    current_seats: int | None
    seats_updated_at: datetime | None
    selected_lead_time_min: LeadTimeMin | None
    forecasts: list[Forecast]


class Recommended(ApiModel):
    route_id: str
    vehicle_id: str | None
    reason_code: ReasonCode


class AlternativeCandidate(ApiModel):
    route_id: str
    route_name: str
    vehicle_id: str | None
    source: BusSource
    station_arrival_at: datetime | None
    destination_arrival_at: datetime | None
    meets_deadline: bool | None
    lead_time_min: LeadTimeMin | None
    no_seat_probability: float | None = Field(ge=0, le=1)
    risk_level: RiskLevel | None


class Alternatives(ApiModel):
    status: AlternativeStatus
    recommended: Recommended | None
    switch_suggested: bool
    candidates: list[AlternativeCandidate]


class SnapshotResponse(ApiModel):
    snapshot_id: UUID
    computed_at: datetime
    next_refresh_at: datetime
    rules_version: str
    data_updated_at: datetime | None
    stale: bool
    service: ServiceInfo
    station: SnapshotStation
    destination: SnapshotDestination
    deadline: str | None
    walk_minutes_allowed: int
    buses: list[Bus]
    alternatives: Alternatives


class ExplanationResponse(ApiModel):
    snapshot_id: UUID
    computed_at: datetime
    source: Literal["llm", "fallback"]
    fallback_reason: FallbackReason | None
    text: str
