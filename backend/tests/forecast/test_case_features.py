"""사례 특징(도착 L분 전 잔여석·앞차 간격). 목표 = G1300 덕현초교(순번 13), 날짜 2026-10-06.

기본 장면(선행시간 5분):
- 내 버스 A: 06:14 에 순번 12 에 처음 닿고 06:20 에 목표(13)에 도착 → 기준 시각 06:15,
  그 시점 기록은 06:15(순번 12, 잔여석 3).
- 앞 버스 B: 06:04 에 순번 12 에 처음 닿았다 → 앞차 간격 10분.
"""

from datetime import datetime, timedelta

import pytest

from app.core.settings import CASE_FEATURE_RULE_VERSION, STALE_AFTER_SEC
from app.forecast.case_features import compute_case_features
from app.forecast.interfaces import CaseFeature
from app.labeling.labels import label_trip
from app.labeling.targets import TargetStop
from app.labeling.trips import Trip
from tests.labeling.helpers import BOARD_STATION_ID, G1300, R1306, VEH_A, at, pos, single_trip

TARGET = TargetStop(route_id=G1300, route_name="G1300", station_id=BOARD_STATION_ID, station_seq=13)
# 합성 차량 ID. 실제 GBIS vehId(2350xxxxx 등)와 겹치지 않게 9 로 시작한다.
VEH_B, VEH_C = "990000002", "990000003"
REFERENCE_AT = at(6, 15)

# (수집 시각, 정류장 순번, 잔여석)
Point = tuple[datetime, int, int | None]

MY_POINTS: tuple[Point, ...] = (
    (at(6, 10), 10, 9),
    (at(6, 13), 11, 7),
    (at(6, 14), 12, 4),  # 순번 12 에 처음 닿음
    (at(6, 15), 12, 3),  # 기준 시각의 기록
    (at(6, 19), 12, 2),  # 기준 시각 뒤. 쓰지 않는다
    (at(6, 20), 13, 5),  # 목표 도착
)
FRONT_POINTS: tuple[Point, ...] = (
    (at(6, 0), 11, 1),
    (at(6, 4), 12, 0),  # 순번 12 에 처음 닿음
    (at(6, 9), 13, 0),
    (at(6, 12), 14, 0),
)


def bus(veh_id: str, points: tuple[Point, ...], **kwargs: object) -> Trip:
    return single_trip(
        *(pos(moment, seq, 0, seat, veh_id=veh_id, **kwargs) for moment, seq, seat in points)
    )


def with_reference_seat(seat: int | None) -> tuple[Point, ...]:
    return tuple(
        (moment, seq, seat if moment == REFERENCE_AT else original)
        for moment, seq, original in MY_POINTS
    )


def features_of(
    mine: Trip, *others: Trip, lead_time_min: int = 5, target: TargetStop = TARGET
) -> CaseFeature:
    return compute_case_features(
        mine, target, lead_time_min, "actual_arrival", day_trips=(mine, *others)
    )


# --- 정상 ----------------------------------------------------------------------
def test_normal_case_has_seats_and_headway() -> None:
    mine = bus(VEH_A, MY_POINTS)
    feature = features_of(mine, bus(VEH_B, FRONT_POINTS))

    assert feature == CaseFeature(
        trip_key=mine.trip_key,
        service_date=mine.service_date,
        route_id=G1300,
        station_id=BOARD_STATION_ID,
        lead_time_min=5,
        reference_at=REFERENCE_AT,
        reference_basis="actual_arrival",
        seats=3,  # 06:19 의 2 나 도착 기록의 5 가 아니다
        headway_min=10.0,
        computable=True,
        uncomputable_reason=None,
        rule_version=CASE_FEATURE_RULE_VERSION,
    )


def test_arrival_matches_label_definition_when_target_is_skipped() -> None:
    # 목표(13)를 건너뛰고 14 로 잡혀도 그 기록이 도착이다(정답 라벨과 같은 정의).
    mine = bus(VEH_A, (*MY_POINTS[:5], (at(6, 20), 14, 5)))
    feature = features_of(mine, bus(VEH_B, FRONT_POINTS))
    label = label_trip(mine, TARGET.station_seq)

    assert label.target_arrival_at == at(6, 20)
    assert feature.reference_at == label.target_arrival_at - timedelta(minutes=5)


def test_headway_uses_latest_vehicle_that_passed_before_mine() -> None:
    older = bus(VEH_C, ((at(5, 50), 11, 6), (at(5, 54), 12, 6), (at(5, 59), 13, 6)))
    feature = features_of(bus(VEH_A, MY_POINTS), older, bus(VEH_B, FRONT_POINTS))

    assert feature.headway_min == 10.0  # 05:54(20분 전)이 아니라 06:04


def test_trial_and_holiday_trips_count_as_preceding_vehicles() -> None:
    front = bus(VEH_B, FRONT_POINTS, mode="trial", is_holiday=True)
    feature = features_of(bus(VEH_A, MY_POINTS), front)

    assert feature.computable
    assert feature.headway_min == 10.0


# --- 잔여석: 0 과 −1 을 섞지 않는다 ------------------------------------------------
def test_zero_seats_is_a_value_not_unknown() -> None:
    feature = features_of(bus(VEH_A, with_reference_seat(0)), bus(VEH_B, FRONT_POINTS))

    assert feature.seats == 0
    assert feature.computable
    assert feature.uncomputable_reason is None


@pytest.mark.parametrize("seat", [-1, None])
def test_unknown_seat_is_not_turned_into_zero(seat: int | None) -> None:
    feature = features_of(bus(VEH_A, with_reference_seat(seat)), bus(VEH_B, FRONT_POINTS))

    assert feature.seats is None
    assert not feature.computable
    assert feature.uncomputable_reason == "seat_unknown"
    assert feature.reference_at == REFERENCE_AT


# --- '그 시점' 기록 --------------------------------------------------------------
@pytest.mark.parametrize(
    ("age_sec", "reason"),
    [(STALE_AFTER_SEC, None), (STALE_AFTER_SEC + 1, "no_record_near_reference")],
)
def test_reference_record_older_than_limit_is_not_used(age_sec: int, reason: str | None) -> None:
    last_before_reference = REFERENCE_AT - timedelta(seconds=age_sec)
    mine = bus(
        VEH_A,
        (
            (at(6, 10), 11, 7),
            (last_before_reference, 12, 3),
            (at(6, 19), 12, 2),
            (at(6, 20), 13, 5),
        ),
    )
    feature = features_of(mine, bus(VEH_B, FRONT_POINTS))

    assert STALE_AFTER_SEC == 60
    assert feature.uncomputable_reason == reason
    assert feature.computable is (reason is None)
    assert feature.seats == (3 if reason is None else None)


def test_no_record_at_or_before_reference() -> None:
    mine = bus(VEH_A, ((at(6, 18), 12, 3), (at(6, 20), 13, 5)))
    feature = features_of(mine, bus(VEH_B, FRONT_POINTS))

    assert feature.uncomputable_reason == "no_record_near_reference"
    assert feature.reference_at == REFERENCE_AT
    assert feature.seats is None


def test_no_target_arrival() -> None:
    mine = bus(VEH_A, MY_POINTS[:5])  # 순번 12 에서 기록이 끝난다
    feature = features_of(mine, bus(VEH_B, FRONT_POINTS))

    assert feature.uncomputable_reason == "no_target_arrival"
    assert feature.reference_at is None
    assert not feature.computable


def test_my_pass_time_not_observed() -> None:
    # 선행시간 10분: 기준 시각 06:10 의 기록이 운행편의 첫 기록(순번 10)이라
    # 순번 10 에 언제 닿았는지 모른다. 잔여석은 알고 있으므로 그대로 담는다.
    feature = features_of(bus(VEH_A, MY_POINTS), bus(VEH_B, FRONT_POINTS), lead_time_min=10)

    assert feature.reference_at == at(6, 10)
    assert feature.uncomputable_reason == "no_record_near_reference"
    assert feature.seats == 9
    assert feature.headway_min is None


# --- 앞차 ----------------------------------------------------------------------
def test_no_preceding_vehicle_keeps_seats() -> None:
    feature = features_of(bus(VEH_A, MY_POINTS))

    assert feature.uncomputable_reason == "no_preceding_vehicle"
    assert not feature.computable
    assert feature.seats == 3
    assert feature.headway_min is None


def test_day_trips_may_be_omitted() -> None:
    feature = compute_case_features(bus(VEH_A, MY_POINTS), TARGET, 5, "actual_arrival")

    assert feature.uncomputable_reason == "no_preceding_vehicle"


@pytest.mark.parametrize(
    "pass_at",
    [
        pytest.param(at(6, 16), id="after-reference"),  # 기준 시각 뒤의 정보는 쓰지 않는다
        pytest.param(at(6, 14, 30), id="after-my-pass"),  # 내 뒤에 지난 차는 앞차가 아니다
    ],
)
def test_vehicle_passing_later_is_not_preceding(pass_at: datetime) -> None:
    later = bus(VEH_B, ((at(6, 11), 11, 6), (pass_at, 12, 6), (at(6, 25), 13, 6)))
    feature = features_of(bus(VEH_A, MY_POINTS), later)

    assert feature.uncomputable_reason == "no_preceding_vehicle"
    assert feature.headway_min is None


def test_vehicle_first_seen_past_the_stop_is_not_a_candidate() -> None:
    # 첫 기록부터 이미 순번 12 이상이면 12 를 언제 지났는지 모른다.
    unseen = bus(VEH_B, ((at(6, 4), 12, 0), (at(6, 9), 13, 0)))
    older = bus(VEH_C, ((at(5, 50), 11, 6), (at(5, 54), 12, 6), (at(5, 59), 13, 6)))

    assert features_of(bus(VEH_A, MY_POINTS), unseen).uncomputable_reason == (
        "no_preceding_vehicle"
    )
    assert features_of(bus(VEH_A, MY_POINTS), unseen, older).headway_min == 20.0


def test_other_route_and_other_day_trips_are_ignored() -> None:
    other_route = bus(VEH_B, FRONT_POINTS, route_id=R1306)
    yesterday = single_trip(
        *(
            pos(moment - timedelta(days=1), seq, 0, seat, veh_id=VEH_C)
            for moment, seq, seat in FRONT_POINTS
        )
    )
    feature = features_of(bus(VEH_A, MY_POINTS), other_route, yesterday)

    assert feature.uncomputable_reason == "no_preceding_vehicle"


# --- 입력 오류 -------------------------------------------------------------------
def test_predicted_arrival_is_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        compute_case_features(bus(VEH_A, MY_POINTS), TARGET, 5, "predicted_arrival")


def test_non_positive_lead_time_raises() -> None:
    with pytest.raises(ValueError):
        compute_case_features(bus(VEH_A, MY_POINTS), TARGET, 0, "actual_arrival")


def test_route_mismatch_raises() -> None:
    target_1306 = TargetStop(
        route_id=R1306, route_name="1306", station_id=BOARD_STATION_ID, station_seq=11
    )
    with pytest.raises(ValueError):
        compute_case_features(bus(VEH_A, MY_POINTS), target_1306, 5, "actual_arrival")
