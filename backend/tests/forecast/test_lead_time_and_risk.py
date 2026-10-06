from dataclasses import dataclass
from datetime import datetime, timedelta

import pytest

from app.core.settings import KST
from app.forecast.lead_time import select_lead_time
from app.forecast.risk import no_seat_probability, risk_level

NOW = datetime(2026, 10, 7, 7, 31, 20, tzinfo=KST)


@dataclass(frozen=True)
class F:
    lead_time_min: int
    issued_at: datetime | None


def forecasts_for_arrival(arrival: datetime, now: datetime = NOW) -> list[F]:
    """도착 L분 전 시점이 지났으면 issued_at 이 있는 예보(15·10·5분)."""
    result = []
    for lead in (15, 10, 5):
        issued = arrival - timedelta(minutes=lead)
        result.append(F(lead, issued if issued <= now else None))
    return result


# ---------------------------------------------------------------------------
# select_lead_time (계약 2.1절)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("minutes_to_arrival", "expected"),
    [
        (20, None),  # 아무 시점도 지나지 않음
        (15, 15),  # 경계: 정확히 15분 전 = 15분 예보를 냄
        (12, 15),  # 계약 예: 12분 전이면 15분 예보
        (10, 10),
        (7, 10),  # 계약 예: 7분 전이면 10분 예보
        (5, 5),
        (1, 5),
    ],
)
def test_select_latest_issued(minutes_to_arrival: int, expected: int | None) -> None:
    arrival = NOW + timedelta(minutes=minutes_to_arrival)

    assert select_lead_time(forecasts_for_arrival(arrival), NOW) == expected


def test_select_ignores_future_issued_at() -> None:
    forecasts = [F(15, NOW - timedelta(minutes=1)), F(10, NOW + timedelta(seconds=1))]

    assert select_lead_time(forecasts, NOW) == 15


def test_select_contract_example() -> None:
    g1300 = forecasts_for_arrival(datetime(2026, 10, 7, 7, 38, 40, tzinfo=KST))
    bus_1306 = forecasts_for_arrival(datetime(2026, 10, 7, 7, 42, tzinfo=KST))

    assert select_lead_time(g1300, NOW) == 10
    assert select_lead_time(bus_1306, NOW) == 15


def test_select_empty_is_none() -> None:
    assert select_lead_time([], NOW) is None


def test_select_unknown_rule_raises() -> None:
    with pytest.raises(ValueError):
        select_lead_time([], NOW, rule="nearest")


# ---------------------------------------------------------------------------
# risk_level (0.7 이상 high, 0.3 미만 low)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("probability", "expected"),
    [
        (0.0, "low"),
        (0.2999, "low"),
        (0.3, "medium"),
        (0.62, "medium"),
        (0.6999, "medium"),
        (0.7, "high"),
        (0.75, "high"),
        (1.0, "high"),
    ],
)
def test_risk_level_boundaries(probability: float, expected: str) -> None:
    assert risk_level(probability) == expected


@pytest.mark.parametrize("probability", [-0.01, 1.01])
def test_risk_level_out_of_range_raises(probability: float) -> None:
    with pytest.raises(ValueError):
        risk_level(probability)


# ---------------------------------------------------------------------------
# no_seat_probability: round(k/n, 4), 위험 등급은 반올림 전 값
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("k", "n", "expected"),
    [
        (16, 26, (0.6154, "medium")),  # 계약 9장 예시
        (18, 24, (0.75, "high")),
        (5, 25, (0.2, "low")),
        (0, 20, (0.0, "low")),
        (20, 20, (1.0, "high")),
    ],
)
def test_no_seat_probability(k: int, n: int, expected: tuple[float, str]) -> None:
    assert no_seat_probability(k, n) == expected


def test_risk_uses_unrounded_probability() -> None:
    # 0.699995 는 4자리 반올림하면 0.7 이지만 반올림 전 값은 0.7 미만이라 medium
    probability, level = no_seat_probability(139_999, 200_000)

    assert probability == 0.7
    assert level == "medium"


@pytest.mark.parametrize(("k", "n"), [(1, 0), (-1, 10), (11, 10)])
def test_no_seat_probability_invalid(k: int, n: int) -> None:
    with pytest.raises(ValueError):
        no_seat_probability(k, n)
