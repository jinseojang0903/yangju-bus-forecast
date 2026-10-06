"""등급(strict·relaxed·unconfirmed)과 정답 라벨 '도착 상태 0석'. 목표 순번 13(직전 12)."""

import pytest

from app.labeling.labels import Grade, TripLabel, UnconfirmedReason, label_trip
from app.labeling.records import PositionRecord
from tests.labeling.helpers import at, pos, single_trip

TARGET_SEQ = 13


def label_of(*records: PositionRecord) -> TripLabel:
    return label_trip(single_trip(*records), TARGET_SEQ)


# --- strict ------------------------------------------------------------------
def test_strict_last_seat_zero_after_departure_is_label_1() -> None:
    label = label_of(
        pos(at(6, 0), 10, 2, 8),
        pos(at(6, 1), 12, 1, 5),
        pos(at(6, 2), 12, 2, 3),  # 직전 정류장 출발
        pos(at(6, 3), 12, 0, 0),  # 도착 전 마지막 잔여석 0
        pos(at(6, 4), 13, 1, 4),  # 도착. 이 값은 쓰지 않는다
    )
    assert label.grade is Grade.STRICT
    assert label.label == 1
    assert label.last_seat == 0
    assert label.last_seat_at == at(6, 3)
    assert label.target_arrival_at == at(6, 4)
    assert label.reason is None
    assert label.is_confirmed


def test_strict_ignores_seat_at_and_after_arrival() -> None:
    label = label_of(
        pos(at(6, 1), 12, 2, 5),
        pos(at(6, 2), 13, 1, 0),  # 도착 기록의 0 은 쓰지 않는다
        pos(at(6, 3), 14, 2, 0),
    )
    assert label.grade is Grade.STRICT
    assert label.label == 0
    assert label.last_seat == 5
    assert label.last_seat_at == at(6, 1)
    assert label.target_arrival_at == at(6, 2)


def test_strict_arrival_is_first_record_at_or_past_target() -> None:
    # 목표(13)를 건너뛰고 14 로 잡혀도 그 기록이 도착이다.
    label = label_of(pos(at(6, 1), 12, 2, 0), pos(at(6, 2), 14, 0, 9))
    assert label.grade is Grade.STRICT
    assert label.label == 1
    assert label.target_arrival_at == at(6, 2)


# --- relaxed -----------------------------------------------------------------
@pytest.mark.parametrize(("seats", "expected"), [((2, 1), 0), ((3, 0), 1)])
def test_relaxed_uses_last_moving_record_at_previous_station(
    seats: tuple[int, int], expected: int
) -> None:
    label = label_of(
        pos(at(6, 0), 11, 2, 9),
        pos(at(6, 1), 12, 0, seats[0]),
        pos(at(6, 2), 12, 0, seats[1]),
        pos(at(6, 3), 13, 1, 7),
    )
    assert label.grade is Grade.RELAXED
    assert label.label == expected
    assert label.last_seat == seats[1]
    assert label.last_seat_at == at(6, 2)
    assert label.reason is None


def test_relaxed_skips_arrived_state_at_previous_station() -> None:
    # 직전 정류장 '도착'(1) 기록은 이동 중 기록이 아니므로 근거가 아니다.
    label = label_of(
        pos(at(6, 1), 12, 0, 4),
        pos(at(6, 2), 12, 1, 0),
        pos(at(6, 3), 13, 1, 0),
    )
    assert label.grade is Grade.RELAXED
    assert label.last_seat == 4
    assert label.label == 0


# --- unconfirmed -------------------------------------------------------------
@pytest.mark.parametrize(
    ("records", "reason"),
    [
        pytest.param(
            (pos(at(6, 0), 11, 2, 5), pos(at(6, 1), 12, 1, 3), pos(at(6, 2), 13, 1, 2)),
            UnconfirmedReason.NO_DEPARTURE_OR_MOVING_RECORD,
            id="only-arrived-at-previous",
        ),
        pytest.param(
            (pos(at(6, 0), 12, None, 3), pos(at(6, 1), 13, 1, 2)),
            UnconfirmedReason.NO_DEPARTURE_OR_MOVING_RECORD,
            id="state-unknown-at-previous",
        ),
        pytest.param(
            (pos(at(6, 0), 10, 0, 3), pos(at(6, 1), 11, 2, 3), pos(at(6, 2), 14, 0, 2)),
            UnconfirmedReason.PASSED_WITHIN_POLL,
            id="jumped-over-previous",
        ),
        pytest.param(
            (pos(at(6, 0), 5, 0, 3), pos(at(6, 1), 9, 2, 3)),
            UnconfirmedReason.NEVER_REACHED_TARGET,
            id="never-reached",
        ),
        pytest.param(
            (pos(at(6, 0), 20, 0, 3), pos(at(6, 1), 25, 0, 2)),
            UnconfirmedReason.NO_RECORD_BEFORE_TARGET,
            id="started-after-target",
        ),
        pytest.param(
            (pos(at(6, 0), 12, 2, -1), pos(at(6, 1), 13, 1, 2)),
            UnconfirmedReason.SEAT_UNKNOWN,
            id="strict-seat-minus-1",
        ),
        pytest.param(
            (pos(at(6, 0), 12, 2, 4), pos(at(6, 1), 12, 0, -1), pos(at(6, 2), 13, 1, 2)),
            UnconfirmedReason.SEAT_UNKNOWN,
            id="strict-last-seat-minus-1-no-fallback",
        ),
        pytest.param(
            (pos(at(6, 0), 12, 0, None), pos(at(6, 1), 13, 1, 2)),
            UnconfirmedReason.SEAT_UNKNOWN,
            id="relaxed-seat-missing",
        ),
    ],
)
def test_unconfirmed_reasons(
    records: tuple[PositionRecord, ...], reason: UnconfirmedReason
) -> None:
    label = label_of(*records)
    assert label.grade is Grade.UNCONFIRMED
    assert label.label is None
    assert label.reason is reason
    assert not label.is_confirmed


def test_seat_unknown_keeps_raw_seat_for_inspection() -> None:
    label = label_of(pos(at(6, 0), 12, 2, -1), pos(at(6, 1), 13, 1, 2))
    assert label.last_seat == -1
    assert label.last_seat_at == at(6, 0)


def test_never_reached_has_no_arrival_time() -> None:
    label = label_of(pos(at(6, 0), 5, 0, 3), pos(at(6, 1), 9, 2, 3))
    assert label.target_arrival_at is None
    assert label.last_seat is None


def test_target_seq_needs_previous_station() -> None:
    with pytest.raises(ValueError):
        label_trip(single_trip(pos(at(6, 0), 1, 2, 3)), 1)


# --- 플래그 --------------------------------------------------------------------
def test_trial_and_holiday_are_flags_and_grade_is_still_computed() -> None:
    label = label_of(
        pos(at(6, 0), 12, 2, 0, mode="trial", interval_sec=40, is_holiday=True),
        pos(at(6, 1), 13, 1, 3, mode="trial", interval_sec=40, is_holiday=True),
    )
    assert label.grade is Grade.STRICT
    assert label.label == 1
    assert label.is_trial
    assert label.is_holiday


def test_regular_run_has_no_flags() -> None:
    label = label_of(pos(at(6, 0), 12, 2, 0), pos(at(6, 1), 13, 1, 3))
    assert not label.is_trial
    assert not label.is_holiday
