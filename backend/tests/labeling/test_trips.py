"""운행편 재구성: (날짜, routeId, vehId) 묶음과 회차·공백 분리, 섞인 수집 간격."""

from datetime import timedelta

from app.labeling.constants import TRIP_SPLIT_GAP_SEC
from app.labeling.labels import Grade, label_trip
from app.labeling.trips import build_trips
from tests.labeling.helpers import R1306, at, pos, single_trip


def test_one_vehicle_two_round_trips_split_on_seq_drop() -> None:
    records = [
        pos(at(6, 0), 11, 2, 9),
        pos(at(6, 1), 12, 2, 3),
        pos(at(6, 2), 13, 1, 3),
        pos(at(6, 20), 30, 1, 40),  # 잠실 회차
        pos(at(6, 45), 51, 1, 45),  # 덕정 쪽 종점
        pos(at(7, 0), 1, 0, 45),  # 기점에서 다시 시작 → 새 운행편
        pos(at(7, 15), 12, 2, 0),
        pos(at(7, 16), 13, 1, 0),
    ]
    trips = build_trips(records)
    assert [t.trip_index for t in trips] == [1, 2]
    assert [t.start_at for t in trips] == [at(6, 0), at(7, 0)]
    assert [len(t.records) for t in trips] == [5, 3]
    assert trips[0].trip_key == "2026-10-06_235000092_235000359_1"

    labels = [label_trip(t, 13) for t in trips]
    assert [(lb.grade, lb.label, lb.last_seat) for lb in labels] == [
        (Grade.STRICT, 0, 3),
        (Grade.STRICT, 1, 0),
    ]


def test_long_gap_splits_trip_but_exact_gap_does_not() -> None:
    gap = timedelta(seconds=TRIP_SPLIT_GAP_SEC)
    split = build_trips(
        [pos(at(6, 0), 5, 0, 9), pos(at(6, 0) + gap + timedelta(seconds=1), 6, 0, 9)]
    )
    assert len(split) == 2
    kept = build_trips([pos(at(6, 0), 5, 0, 9), pos(at(6, 0) + gap, 6, 0, 9)])
    assert len(kept) == 1


def test_small_backward_jitter_stays_in_same_trip() -> None:
    trip = single_trip(
        pos(at(6, 0), 12, 2, 4),
        pos(at(6, 1), 11, 0, 4),  # 위치 보정으로 한 순번 뒤로
        pos(at(6, 2), 13, 1, 2),
    )
    assert [r.station_seq for r in trip.records] == [12, 11, 13]


def test_groups_by_route_and_vehicle_and_sorts_by_time() -> None:
    records = [
        pos(at(6, 2), 13, 1, 1, veh_id="235000001"),
        pos(at(6, 1), 12, 2, 0, veh_id="235000001"),
        pos(at(6, 0), 10, 0, 5, veh_id="235000002"),
        pos(at(6, 0), 10, 0, 5, veh_id="235000001", route_id=R1306),
    ]
    trips = build_trips(reversed(records))
    keys = [(t.route_id, t.veh_id) for t in trips]
    assert keys == [
        ("235000092", "235000002"),
        ("235000092", "235000001"),
        ("235000123", "235000001"),
    ]
    g1300_a = trips[1]
    assert [r.collected_at for r in g1300_a.records] == [at(6, 1), at(6, 2)]
    assert all(t.trip_index == 1 for t in trips)


def test_mixed_poll_intervals_build_one_trip_and_label() -> None:
    trip = single_trip(
        pos(at(6, 0, 0), 10, 0, 6, interval_sec=60),
        pos(at(6, 0, 10), 11, 2, 5, interval_sec=10),
        pos(at(6, 0, 40), 12, 2, 2, interval_sec=30),
        pos(at(6, 1, 20), 12, 0, 0, interval_sec=40),
        pos(at(6, 2, 20), 13, 1, 0, interval_sec=60),
    )
    assert trip.interval_secs == (10, 30, 40, 60)
    label = label_trip(trip, 13)
    assert label.interval_sec == (10, 30, 40, 60)
    assert (label.grade, label.label, label.last_seat_at) == (Grade.STRICT, 1, at(6, 1, 20))


def test_interval_sec_single_value_or_missing() -> None:
    single = label_trip(single_trip(pos(at(6, 0), 12, 2, 1), pos(at(6, 1), 13, 1, 1)), 13)
    assert single.interval_sec == 60
    legacy = label_trip(
        single_trip(
            pos(at(6, 0), 12, 2, 1, interval_sec=None), pos(at(6, 1), 13, 1, 1, interval_sec=None)
        ),
        13,
    )
    assert legacy.interval_sec is None
