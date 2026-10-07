from dataclasses import dataclass
from datetime import datetime

import pytest

from app.core.settings import KST
from app.forecast.alternatives import (
    Recommendation,
    decide_alternatives,
    meets_deadline,
    resolve_deadline,
)
from app.forecast.risk import risk_level
from app.schemas.common import BusSource, RiskLevel


def at(hour: int, minute: int) -> datetime:
    return datetime(2026, 10, 7, hour, minute, tzinfo=KST)


DEADLINE = at(8, 30)
UNDECIDABLE = ("undecidable", None, False)


@dataclass(frozen=True)
class C:
    route_id: str
    station_arrival_at: datetime | None
    destination_arrival_at: datetime | None
    no_seat_probability: float | None
    vehicle_id: str | None = None
    source: BusSource = "arrival_1st"

    @property
    def risk_level(self) -> RiskLevel | None:
        p = self.no_seat_probability
        return risk_level(p) if p is not None else None


G1300 = C("G1300", at(7, 38), at(8, 21), 0.75, "v1", "arrival_1st")
B1306 = C("1306", at(7, 42), at(8, 27), 0.2, "v2", "arrival_2nd")


# ---------------------------------------------------------------------------
# 6장 1~5단계
# ---------------------------------------------------------------------------
def test_step1_no_candidates_is_undecidable() -> None:
    assert decide_alternatives([], DEADLINE) == UNDECIDABLE


def test_step2_all_destination_unknown_is_arrival_unavailable() -> None:
    candidates = [C("a", at(7, 38), None, 0.2), C("b", at(7, 42), None, 0.1)]

    assert decide_alternatives(candidates, DEADLINE) == ("arrival_unavailable", None, False)


def test_step2_skipped_without_deadline() -> None:
    candidates = [C("a", at(7, 38), None, 0.2), C("b", at(7, 42), None, 0.1, None, "arrival_2nd")]

    status, recommendation, _ = decide_alternatives(candidates, None)

    assert status == "recommended"
    assert recommendation == Recommendation("b", None, "arrival_2nd", "lowest_risk")


def test_step3_all_known_over_deadline_is_no_alternative() -> None:
    candidates = [C("a", at(7, 38), at(8, 31), 0.2), C("b", at(7, 42), at(8, 40), 0.1)]

    assert decide_alternatives(candidates, DEADLINE) == ("no_alternative", None, False)


def test_step3_not_applied_when_some_destination_unknown() -> None:
    # 아는 후보는 마감을 넘지만 모르는 후보가 있어 마감 판정을 끝낼 수 없음 → 5단계
    candidates = [C("a", at(7, 38), at(8, 35), 0.2), C("b", at(7, 42), None, 0.1)]

    assert decide_alternatives(candidates, DEADLINE) == UNDECIDABLE


def test_step4_recommends_lowest_probability_in_deadline() -> None:
    status, recommendation, switch = decide_alternatives([G1300, B1306], DEADLINE)

    assert status == "recommended"
    assert recommendation == Recommendation("1306", "v2", "arrival_2nd", "lowest_risk")
    assert switch is True  # 계약 9장 예시


def test_step4_excludes_candidates_over_deadline() -> None:
    late_but_safe = C("late", at(7, 50), at(8, 45), 0.0)

    _, recommendation, _ = decide_alternatives([G1300, late_but_safe], DEADLINE)

    assert recommendation is not None and recommendation.route_id == "G1300"


def test_step4_with_unknown_candidate_mixed_still_recommends() -> None:
    unknown = C("unknown", at(7, 45), None, 0.0)

    status, recommendation, _ = decide_alternatives([G1300, unknown], DEADLINE)

    assert status == "recommended"
    assert recommendation is not None and recommendation.route_id == "G1300"


def test_step4_tie_prefers_earliest_arrival() -> None:
    later = C("later", at(7, 50), at(8, 20), 0.2)
    earlier = C("earlier", at(7, 40), at(8, 25), 0.2)

    _, recommendation, _ = decide_alternatives([later, earlier], DEADLINE)

    assert recommendation == Recommendation(
        "earlier", None, "arrival_1st", "earliest_among_equal_risk"
    )


def test_step4_ignores_candidates_without_probability() -> None:
    # 확률이 없는 후보를 '위험이 낮다'로 보지 않는다
    no_probability = C("none", at(7, 35), at(8, 10), None)

    _, recommendation, _ = decide_alternatives([no_probability, G1300], DEADLINE)

    assert recommendation is not None and recommendation.route_id == "G1300"


def test_step5_in_deadline_but_no_probability_is_undecidable() -> None:
    candidates = [C("a", at(7, 38), at(8, 21), None), C("b", at(7, 42), at(8, 27), None)]

    assert decide_alternatives(candidates, DEADLINE) == UNDECIDABLE


def test_step5_probability_only_outside_deadline_is_undecidable() -> None:
    candidates = [C("a", at(7, 38), at(8, 21), None), C("b", at(7, 42), at(8, 45), 0.1)]

    assert decide_alternatives(candidates, DEADLINE)[0] == "undecidable"


# ---------------------------------------------------------------------------
# switchSuggested
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("first_probability", "other_probability", "expected"),
    [
        (0.75, 0.2, True),  # 1순위 high, 추천은 low
        (0.75, 0.5, True),  # 추천은 medium
        (0.9, 0.72, False),  # 추천도 high → 더 낮은 등급이 아님
        (0.6, 0.2, False),  # 1순위가 high 가 아님
        (0.75, None, False),  # 추천이 1순위 자신
    ],
)
def test_switch_suggested(
    first_probability: float, other_probability: float | None, expected: bool
) -> None:
    first = C("first", at(7, 38), at(8, 21), first_probability)
    other = C("other", at(7, 42), at(8, 27), other_probability, None, "arrival_2nd")

    _, _, switch = decide_alternatives([first, other], DEADLINE)

    assert switch is expected


def test_first_bus_is_earliest_arrival_regardless_of_order() -> None:
    # 입력 순서가 뒤바뀌어도 도착 1순위는 G1300(07:38) → switch true
    assert decide_alternatives([B1306, G1300], DEADLINE)[2] is True
    # 도착 시각을 모르는 후보는 1순위가 될 수 없다
    unknown_high = C("unknown", None, at(8, 0), 0.9, None, "timetable_next")
    assert decide_alternatives([unknown_high, B1306], DEADLINE)[2] is False


# ---------------------------------------------------------------------------
# 마감 판정
# ---------------------------------------------------------------------------
def test_meets_deadline_boundaries() -> None:
    assert meets_deadline(at(8, 30), DEADLINE) is True
    assert meets_deadline(at(8, 31), DEADLINE) is False
    assert meets_deadline(at(8, 26), DEADLINE, walk_minutes=4) is True
    assert meets_deadline(at(8, 26), DEADLINE, walk_minutes=5) is False
    assert meets_deadline(None, DEADLINE) is None
    assert meets_deadline(at(8, 0), None) is None


def test_resolve_deadline_uses_same_kst_date() -> None:
    computed_at = datetime(2026, 10, 7, 7, 31, 20, tzinfo=KST)

    assert resolve_deadline("08:30", computed_at) == at(8, 30)
