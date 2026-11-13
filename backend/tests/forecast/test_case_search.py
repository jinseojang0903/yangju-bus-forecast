"""사례 검색(CLAUDE.md '사례 검색과 확률'). 규칙 값은 settings.FORECAST_RULES 그대로 쓴다.

조회 특징: G1300 덕현초교, 선행시간 10분, 기준 시각 2026-10-20 07:00, 잔여석 5, 앞차 간격 10분.
"""

from datetime import datetime, timedelta

import pytest

from app.core.settings import FORECAST_RULES, KST
from app.forecast.case_search import search_cases
from app.forecast.interfaces import CaseFeature, LabeledCase, UncomputableReason
from tests.labeling.helpers import BOARD_STATION_ID, G1300, R1306

REFERENCE_AT = datetime(2026, 10, 20, 7, 0, tzinfo=KST)
DAY_BEFORE = REFERENCE_AT - timedelta(days=1)


def feature(
    seats: int | None = 5,
    headway_min: float | None = 10.0,
    *,
    trip_key: str = "query",
    route_id: str = G1300,
    station_id: str = BOARD_STATION_ID,
    lead_time_min: int = 10,
    reference_at: datetime | None = REFERENCE_AT,
    uncomputable_reason: UncomputableReason | None = None,
) -> CaseFeature:
    return CaseFeature(
        trip_key=trip_key,
        service_date=REFERENCE_AT.date(),
        route_id=route_id,
        station_id=station_id,
        lead_time_min=lead_time_min,
        reference_at=reference_at,
        reference_basis="actual_arrival",
        seats=seats,
        headway_min=headway_min,
        computable=uncomputable_reason is None,
        uncomputable_reason=uncomputable_reason,
        rule_version="test",
    )


def case(
    trip_key: str,
    seats: int | None = 5,
    headway_min: float | None = 10.0,
    *,
    label: int | None = 0,
    confirmed_at: datetime | None = DAY_BEFORE,
    **feature_kwargs: object,
) -> LabeledCase:
    return LabeledCase(
        feature=feature(seats, headway_min, trip_key=trip_key, **feature_kwargs),
        label=label,
        label_grade="unconfirmed" if label is None else "strict",
        label_rule_version="test",
        confirmed_at=confirmed_at,
    )


def keys(history: list[LabeledCase], query: CaseFeature | None = None) -> tuple[str, ...]:
    return search_cases(query or feature(), history, FORECAST_RULES).case_trip_keys


def identical_cases(count: int, *, zero_seat_count: int = 0) -> list[LabeledCase]:
    """조회 특징과 똑같은 사례 count 건. 번호가 클수록 최근이고, 앞의 zero_seat_count 건이 0석."""
    return [
        case(
            f"case-{index:02d}",
            label=1 if index < zero_seat_count else 0,
            confirmed_at=DAY_BEFORE + timedelta(minutes=index),
        )
        for index in range(count)
    ]


# --- 허용폭(경계 포함) -------------------------------------------------------------
def test_rule_values_are_the_fixed_ones() -> None:
    assert (FORECAST_RULES.seat_tolerance, FORECAST_RULES.headway_tolerance_min) == (2, 3)
    assert (FORECAST_RULES.max_cases, FORECAST_RULES.min_cases_to_show) == (30, 20)


@pytest.mark.parametrize(
    ("seats", "headway_min", "is_included"),
    [
        (7, 10.0, True),  # +2석
        (3, 10.0, True),  # −2석
        (8, 10.0, False),  # +3석
        (2, 10.0, False),  # −3석
        (5, 13.0, True),  # +3분
        (5, 7.0, True),  # −3분
        (5, 13.5, False),
        (5, 6.5, False),
        (7, 13.0, True),  # 둘 다 경계
        (7, 13.5, False),  # 하나라도 벗어나면 제외
    ],
)
def test_tolerance_boundaries(seats: int, headway_min: float, is_included: bool) -> None:
    assert keys([case("a", seats, headway_min)]) == (("a",) if is_included else ())


# 간격은 실제로 '초 ÷ 60' 으로 계산되므로 분 단위 float 오차가 생긴다.
# 예: 310/60 − 130/60 = 3.0000000000000004. 정확히 180초 차이는 경계 안이어야 한다.
@pytest.mark.parametrize(("query_sec", "case_sec"), [(310, 130), (340, 160), (250.7, 70.7)])
def test_exactly_three_minutes_apart_in_seconds_is_included(
    query_sec: float, case_sec: float
) -> None:
    assert abs(query_sec / 60 - case_sec / 60) != 3  # 오차가 실제로 있는 값이다
    query = feature(headway_min=query_sec / 60)

    assert keys([case("earlier", headway_min=case_sec / 60)], query) == ("earlier",)
    reverse = feature(headway_min=case_sec / 60)
    assert keys([case("later", headway_min=query_sec / 60)], reverse) == ("later",)


def test_every_180_second_gap_is_included_and_181_is_not() -> None:
    for query_sec in range(181, 1800):
        query = feature(headway_min=query_sec / 60)
        inside = case("inside", headway_min=(query_sec - 180) / 60)
        outside = case("outside", headway_min=(query_sec - 181) / 60)

        assert keys([inside, outside], query) == ("inside",), query_sec


def test_equal_distance_is_not_broken_by_float_error() -> None:
    # 간격 90초 차(1.5분 ÷ 3 = 0.5)와 잔여석 1석 차(1 ÷ 2 = 0.5)는 거리가 같다.
    # float 로 계산하면 310/60 − 220/60 = 1.5000000000000004 라 간격 쪽이 멀어 보인다.
    query = feature(5, 310 / 60)
    history = [
        case("seat-1-old", 6, 310 / 60, confirmed_at=DAY_BEFORE - timedelta(hours=1)),
        case("headway-90s-recent", 5, 220 / 60, confirmed_at=DAY_BEFORE),
    ]

    assert keys(history, query) == ("headway-90s-recent", "seat-1-old")
    assert keys(history[::-1], query) == ("headway-90s-recent", "seat-1-old")


# --- 거리와 순서 -----------------------------------------------------------------
def test_cases_are_ordered_by_distance() -> None:
    history = [
        case("seat-2", 7, 10.0),  # 2 ÷ 2 = 1.0
        case("seat-1-headway-3", 4, 13.0),  # 1 ÷ 2 + 3 ÷ 3 = 1.5
        case("headway-1.5", 5, 11.5),  # 1.5 ÷ 3 = 0.5
        case("same", 5, 10.0),  # 0
    ]

    assert keys(history) == ("same", "headway-1.5", "seat-2", "seat-1-headway-3")


def test_equal_distance_prefers_recent_trip() -> None:
    history = [
        case("old", 7, 10.0, confirmed_at=DAY_BEFORE - timedelta(days=1)),  # 거리 1.0
        case("recent", 5, 13.0, confirmed_at=DAY_BEFORE),  # 거리 1.0
        case("middle", 3, 10.0, confirmed_at=DAY_BEFORE - timedelta(hours=1)),  # 거리 1.0
    ]

    assert keys(history) == ("recent", "middle", "old")
    assert keys(history[::-1]) == ("recent", "middle", "old")  # 입력 순서와 무관


def test_one_case_per_trip() -> None:
    history = [case("dup", 7, 10.0, label=1), case("dup", 5, 10.0, label=0), case("other", 6, 10.0)]
    result = search_cases(feature(), history, FORECAST_RULES)

    assert result.case_trip_keys == ("dup", "other")  # dup 은 가까운 쪽(거리 0) 1건만
    assert (result.n, result.k) == (2, 0)


def test_at_most_max_cases_and_k_counts_only_selected() -> None:
    # 35건 중 가장 오래된 5건만 0석이다. 거리가 같아 최근 30건이 뽑히므로 k 는 0 이다.
    result = search_cases(feature(), identical_cases(35, zero_seat_count=5), FORECAST_RULES)

    assert (result.n, result.k) == (30, 0)
    assert result.case_trip_keys[0] == "case-34"
    assert result.case_trip_keys[-1] == "case-05"
    assert result.status == "ok"
    assert result.no_seat_probability == 0.0


def test_nearer_cases_win_over_recent_ones_when_over_max() -> None:
    far_but_recent = [
        case(f"far-{index}", 7, 10.0, confirmed_at=REFERENCE_AT - timedelta(minutes=1 + index))
        for index in range(5)
    ]
    result = search_cases(feature(), far_but_recent + identical_cases(30), FORECAST_RULES)

    assert result.n == 30
    assert not any(key.startswith("far-") for key in result.case_trip_keys)


# --- 표시 하한과 확률 -------------------------------------------------------------
def test_below_minimum_is_insufficient_but_keeps_n_and_k() -> None:
    result = search_cases(feature(), identical_cases(19, zero_seat_count=7), FORECAST_RULES)

    assert result.status == "insufficient_cases"
    assert (result.n, result.k) == (19, 7)
    assert len(result.case_trip_keys) == 19
    assert result.no_seat_probability is None


def test_minimum_cases_gives_unsmoothed_ratio() -> None:
    result = search_cases(feature(), identical_cases(20, zero_seat_count=14), FORECAST_RULES)

    assert result.status == "ok"
    assert (result.n, result.k) == (20, 14)
    assert result.no_seat_probability == 14 / 20


@pytest.mark.parametrize(("zero_seat_count", "expected"), [(0, 0.0), (20, 1.0)])
def test_extreme_ratios_are_not_smoothed(zero_seat_count: int, expected: float) -> None:
    history = identical_cases(20, zero_seat_count=zero_seat_count)

    assert search_cases(feature(), history, FORECAST_RULES).no_seat_probability == expected


# --- 필터 ----------------------------------------------------------------------
def test_only_cases_confirmed_before_reference_time() -> None:
    history = [
        case("same-day-earlier", confirmed_at=REFERENCE_AT - timedelta(seconds=1)),
        case("at-reference", confirmed_at=REFERENCE_AT),
        case("later", confirmed_at=REFERENCE_AT + timedelta(minutes=10)),
        case("next-day", confirmed_at=REFERENCE_AT + timedelta(days=1)),
        case("no-confirmed-time", confirmed_at=None),
    ]

    assert keys(history) == ("same-day-earlier",)


def test_unconfirmed_and_uncomputable_cases_are_excluded() -> None:
    history = [
        case("unconfirmed", label=None),
        case("no-headway", 5, None, uncomputable_reason="no_preceding_vehicle"),
        case("seat-unknown", None, 10.0, uncomputable_reason="seat_unknown"),
        case("usable", label=1),
    ]
    result = search_cases(feature(), history, FORECAST_RULES)

    assert result.case_trip_keys == ("usable",)
    assert (result.n, result.k) == (1, 1)


def test_other_route_station_and_lead_time_are_excluded() -> None:
    history = [
        case("other-route", route_id=R1306),
        case("other-station", station_id="235000409"),
        case("other-lead-time", lead_time_min=5),
        case("match"),
    ]

    assert keys(history) == ("match",)


# --- 입력 누락 -------------------------------------------------------------------
@pytest.mark.parametrize(
    "query",
    [
        feature(None, 10.0, uncomputable_reason="seat_unknown"),
        feature(5, None, uncomputable_reason="no_preceding_vehicle"),
        feature(None, None, reference_at=None, uncomputable_reason="no_target_arrival"),
    ],
)
def test_uncomputable_feature_is_missing_input(query: CaseFeature) -> None:
    result = search_cases(query, identical_cases(25, zero_seat_count=25), FORECAST_RULES)

    assert result.status == "missing_input"
    assert (result.n, result.k) == (0, 0)
    assert result.case_trip_keys == ()
    assert result.no_seat_probability is None


def test_empty_history_is_insufficient() -> None:
    result = search_cases(feature(), [], FORECAST_RULES)

    assert result.status == "insufficient_cases"
    assert (result.n, result.k) == (0, 0)
