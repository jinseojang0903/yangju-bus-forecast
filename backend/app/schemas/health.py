"""GET /health, GET /health/detail 응답(계약 4.1·4.2절)."""

from datetime import datetime

from app.schemas.common import ApiModel, HealthStatus


class HealthResponse(ApiModel):
    status: HealthStatus
    now: datetime
    last_success_at: datetime | None


class ApiCounts(ApiModel):
    """계약의 Record<"location" | "arrival", number>."""

    location: int
    arrival: int


class CollectorToday(ApiModel):
    date: str  # "YYYY-MM-DD"
    is_holiday: bool
    calls: ApiCounts
    failures: ApiCounts
    quota_exceeded: bool
    throttled: bool


class CollectorTarget(ApiModel):
    name: str
    interval_sec: int
    calls: int
    failures: int
    empty: int
    skipped_cycles: int
    last_success_at: datetime | None


class CollectorDetail(ApiModel):
    in_window: bool
    last_success_at: datetime | None
    today: CollectorToday
    targets: list[CollectorTarget]


class HealthDetailResponse(ApiModel):
    status: HealthStatus
    now: datetime
    collector: CollectorDetail
