"""대안 결정 규칙(계약 6장, CLAUDE.md '도착 시각과 대안 추천')."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Protocol

from app.core.settings import COLLECT_TARGET, KST
from app.forecast.risk import RISK_RANK
from app.schemas.common import AlternativeStatus, BusSource, ReasonCode, RiskLevel


class CandidateLike(Protocol):
    """대안 후보 한 대. API 의 AlternativeCandidate 모델도 이 모양을 만족한다.

    no_seat_probability·risk_level 은 그 버스의 selectedLeadTimeMin 예보 값이다.
    """

    @property
    def route_id(self) -> str: ...

    @property
    def vehicle_id(self) -> str | None: ...

    @property
    def source(self) -> BusSource: ...

    @property
    def station_arrival_at(self) -> datetime | None: ...

    @property
    def destination_arrival_at(self) -> datetime | None: ...

    @property
    def no_seat_probability(self) -> float | None: ...

    @property
    def risk_level(self) -> RiskLevel | None: ...


@dataclass(frozen=True)
class Recommendation:
    """추천 결과. source 는 API 에 없지만 DB forecast_group.recommended_source 에 남긴다
    (차량이 없는 시간표상 다음 차를 forecast_snapshot 행과 맞추려고)."""

    route_id: str
    vehicle_id: str | None
    source: BusSource
    reason_code: ReasonCode


def resolve_deadline(deadline: str, on: datetime) -> datetime:
    """마감 "HH:MM"(KST)을 on 과 같은 KST 날짜의 시각으로 바꾼다. 형식은 경계에서 검증한다."""
    hour, minute = (int(part) for part in deadline.split(":"))
    local = on.astimezone(KST)
    return local.replace(hour=hour, minute=minute, second=0, microsecond=0)


def meets_deadline(
    destination_arrival_at: datetime | None,
    deadline_at: datetime | None,
    walk_minutes: int = COLLECT_TARGET.walk_minutes_allowed,
) -> bool | None:
    """목적지 도착 + 허용 보행시간 ≤ 마감이면 True. 마감이나 도착 시각이 없으면 None."""
    if deadline_at is None or destination_arrival_at is None:
        return None
    return destination_arrival_at + timedelta(minutes=walk_minutes) <= deadline_at


def _same_bus(a: CandidateLike, b: CandidateLike) -> bool:
    return (a.route_id, a.vehicle_id, a.source, a.station_arrival_at) == (
        b.route_id,
        b.vehicle_id,
        b.source,
        b.station_arrival_at,
    )


def _arrival_sort_key(candidate: CandidateLike) -> tuple[bool, datetime]:
    # 도착 시각을 모르는 후보는 맨 뒤로.
    arrival = candidate.station_arrival_at
    return (arrival is None, arrival or datetime.max.replace(tzinfo=KST))


def decide_alternatives(
    candidates: Sequence[CandidateLike],
    deadline: datetime | None,
    walk_minutes: int = COLLECT_TARGET.walk_minutes_allowed,
) -> tuple[AlternativeStatus, Recommendation | None, bool]:
    """계약 6장 순서대로 검사해 (대안 상태, 추천, switchSuggested) 를 돌려준다.

    1. 후보가 없다 → undecidable
    2. deadline 이 있는데 모든 후보의 destination_arrival_at 이 None → arrival_unavailable
    3. deadline 이 있고 도착 시각이 있는 후보가 모두 마감을 넘고, 도착 시각을 모르는 후보도
       없다 → no_alternative
    4. 마감 안 후보(deadline 이 없으면 모든 후보) 중 확률이 있는 후보가 있다 → recommended.
       확률이 가장 낮은 차, 같으면 먼저 오는 차(reason_code earliest_among_equal_risk)
    5. 그 밖 → undecidable

    확률이 없는 후보는 추천 대상에서 빼지만 '위험이 낮다'로 보지 않는다.
    switchSuggested: 도착 1순위 버스의 위험이 high 이고, 추천 차가 그 버스가 아니며
    추천 차의 위험 등급이 더 낮을 때 True. 도착 1순위는 입력 순서와 무관하게 후보를
    station_arrival_at 순(모르는 후보는 뒤)으로 정렬해 맨 앞 후보로 정한다.
    deadline 은 resolve_deadline 으로 바꾼 시각이다.
    """
    if not candidates:
        return "undecidable", None, False

    judged = [
        (c, meets_deadline(c.destination_arrival_at, deadline, walk_minutes)) for c in candidates
    ]
    if deadline is not None:
        if all(c.destination_arrival_at is None for c in candidates):
            return "arrival_unavailable", None, False
        has_unknown = any(c.destination_arrival_at is None for c in candidates)
        if not has_unknown and not any(meets for _, meets in judged):
            return "no_alternative", None, False
        in_deadline = [c for c, meets in judged if meets is True]
    else:
        in_deadline = list(candidates)

    scored = [(c, c.no_seat_probability) for c in in_deadline if c.no_seat_probability is not None]
    if not scored:
        return "undecidable", None, False

    lowest = min(probability for _, probability in scored)
    tied = sorted((c for c, probability in scored if probability == lowest), key=_arrival_sort_key)
    chosen = tied[0]
    reason: ReasonCode = "earliest_among_equal_risk" if len(tied) > 1 else "lowest_risk"
    recommendation = Recommendation(chosen.route_id, chosen.vehicle_id, chosen.source, reason)
    first_bus = min(candidates, key=_arrival_sort_key)
    return "recommended", recommendation, _switch_suggested(first_bus, chosen)


def _switch_suggested(first_bus: CandidateLike, chosen: CandidateLike) -> bool:
    if first_bus.risk_level != "high":
        return False
    if _same_bus(first_bus, chosen) or chosen.risk_level is None:
        return False
    return RISK_RANK[chosen.risk_level] < RISK_RANK["high"]
