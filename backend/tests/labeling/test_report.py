"""평가용 라벨 고르기와 노선별 집계."""

from app.labeling.labels import Grade, UnconfirmedReason, label_trip
from app.labeling.report import TOTAL_ROUTE_NAME, select_labels, summarize
from tests.labeling.helpers import G1300, R1306, at, pos, single_trip


def _labels() -> list:
    def lab(*records):
        return label_trip(single_trip(*records), 13)

    return [
        lab(pos(at(6, 0), 12, 2, 0, veh_id="1"), pos(at(6, 1), 13, 1, 0, veh_id="1")),
        lab(pos(at(6, 0), 12, 0, 3, veh_id="2"), pos(at(6, 1), 13, 1, 0, veh_id="2")),
        lab(pos(at(6, 0), 12, 2, 4, veh_id="3", mode="trial"), pos(at(6, 1), 13, 1, 0, veh_id="3")),
        lab(
            pos(at(6, 0), 12, 2, 4, veh_id="4", is_holiday=True),
            pos(at(6, 1), 13, 1, 0, veh_id="4"),
        ),
        lab(pos(at(6, 0), 10, 0, 3, veh_id="5"), pos(at(6, 1), 14, 1, 0, veh_id="5")),
        lab(pos(at(6, 0), 5, 0, 3, veh_id="6")),
    ]


def test_select_labels_drops_unconfirmed_and_filters_by_grade_and_flags() -> None:
    labels = _labels()
    both = select_labels(
        labels, grades=(Grade.STRICT, Grade.RELAXED), include_trial=False, include_holiday=False
    )
    assert [lb.veh_id for lb in both] == ["1", "2"]

    strict_all = select_labels(
        labels, grades=(Grade.STRICT,), include_trial=True, include_holiday=True
    )
    assert [lb.veh_id for lb in strict_all] == ["1", "3", "4"]

    # 미확인은 grades 에 넣어도 빠진다.
    assert select_labels(labels, grades=tuple(Grade), include_trial=True, include_holiday=True) == [
        lb for lb in labels if lb.is_confirmed
    ]


def test_summarize_counts_and_reached_denominator() -> None:
    summaries = summarize(_labels(), {G1300: "G1300", R1306: "1306"})
    assert [s.route_name for s in summaries] == ["G1300", "1306", TOTAL_ROUTE_NAME]
    g1300, r1306, total = summaries
    assert g1300.trips == 6
    assert g1300.by_grade[Grade.STRICT] == 3
    assert g1300.by_grade[Grade.RELAXED] == 1
    assert g1300.by_grade[Grade.UNCONFIRMED] == 2
    assert g1300.zero_by_grade[Grade.STRICT] == 1
    assert g1300.by_reason[UnconfirmedReason.PASSED_WITHIN_POLL] == 1
    assert g1300.by_reason[UnconfirmedReason.NEVER_REACHED_TARGET] == 1
    assert g1300.reached_trips == 5
    assert (g1300.trial_trips, g1300.holiday_trips) == (1, 1)
    assert r1306.trips == 0
    assert total.trips == 6
