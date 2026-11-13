"""백테스트 핵심 계산과 집계. 기대값의 손 계산은 tests/evaluation/helpers.py 설명에 있다."""

from datetime import date, time
from pathlib import Path

import pytest

from app.core.settings import (
    FORECAST_RULES,
    LABEL_RULE_VERSION,
    MODE_TRIAL,
    PUBLISH_CRITERIA,
    RULES_VERSION,
    SERVICE_HOURS,
    ForecastRules,
)
from app.evaluation.backtest import (
    BacktestResult,
    BacktestRow,
    Exclusion,
    classify_trip,
    evaluate_labeled_trips,
    label_trips,
    run_backtest,
)
from app.evaluation.metrics import (
    Confusion,
    MethodSummary,
    ZeroRateReference,
    summarize_methods,
    summarize_sample,
    tally,
)
from app.evaluation.report import NOT_AVAILABLE, RunInfo, format_report
from app.labeling.labels import Grade, UnconfirmedReason, label_trip
from app.labeling.records import PositionRecord
from app.labeling.trips import build_trips
from tests.evaluation.helpers import (
    BASE_DAYS,
    HOLIDAY,
    ROUTE_NAMES,
    TARGET_STOPS,
    TEST_DAY,
    build_scenario_records,
    moment,
    trip_records,
)
from tests.labeling.helpers import G1300, pos


@pytest.fixture(scope="module")
def scenario() -> BacktestResult:
    records = [r for day_records in build_scenario_records().values() for r in day_records]
    return run_backtest(build_trips(records), TARGET_STOPS)


def method(result: BacktestResult, lead_time_min: int, route_name: str) -> MethodSummary:
    summaries = summarize_methods(result.rows, ROUTE_NAMES, result.lead_times_min)
    return next(
        s for s in summaries if (s.lead_time_min, s.route_name) == (lead_time_min, route_name)
    )


def row_at(result: BacktestResult, day: date, arrival: time, lead_time_min: int) -> BacktestRow:
    return next(
        row
        for row in result.rows
        if row.target_arrival_at == moment(day, arrival)
        and row.forecast.route_id == G1300
        and row.forecast.lead_time_min == lead_time_min
    )


def counts(confusion: Confusion) -> tuple[int, int, int, int]:
    return confusion.tp, confusion.fn, confusion.fp, confusion.tn


# --- 고정 규칙 값 ------------------------------------------------------------------
def test_scenario_assumes_the_fixed_rule_values() -> None:
    assert FORECAST_RULES.lead_times_min == (5, 10, 15)
    assert (FORECAST_RULES.min_cases_to_show, FORECAST_RULES.max_cases) == (20, 30)
    assert PUBLISH_CRITERIA.alert_probability_min == 0.7
    assert (SERVICE_HOURS.forecast_arrival_start, SERVICE_HOURS.forecast_arrival_end) == (
        time(6, 0),
        time(9, 0),
    )


# --- 표본 ----------------------------------------------------------------------
def test_sample_counts(scenario: BacktestResult) -> None:
    g1300, r1306, total = summarize_sample(scenario.selections, ROUTE_NAMES)

    assert g1300.trips == 10 * 6 + 10 + 6  # 기본 날 + 시험 날 + 공휴일
    assert g1300.by_exclusion == {
        Exclusion.TRIAL: 1,  # 시험 날 X
        Exclusion.NON_SERVICE_DAY: 6,  # 공휴일 운행편 전부
        Exclusion.OUTSIDE_FORECAST_HOURS: 11,  # 날마다 05:50 도착 P
    }
    assert (g1300.targets, g1300.zero_arrivals) == (58, 23)
    assert g1300.by_grade == {Grade.STRICT: 58}
    assert (r1306.trips, r1306.targets, r1306.zero_arrivals) == (1, 1, 1)
    assert (total.trips, total.targets, total.zero_arrivals) == (77, 59, 24)


def test_one_row_per_trip_and_lead_time(scenario: BacktestResult) -> None:
    keys = [(row.forecast.trip_key, row.forecast.lead_time_min) for row in scenario.rows]

    assert len(keys) == 59 * 3
    assert len(set(keys)) == len(keys)


def test_holiday_and_trial_trips_are_neither_targets_nor_cases(scenario: BacktestResult) -> None:
    trial_arrival = moment(TEST_DAY, time(7, 50))
    used_case_keys = {key for row in scenario.rows for key in row.forecast.case_trip_keys}
    excluded_keys = {
        s.label.trip_key
        for s in scenario.selections
        if s.exclusion in (Exclusion.TRIAL, Exclusion.NON_SERVICE_DAY)
    }

    assert len(excluded_keys) == 7
    assert not any(row.forecast.service_date == HOLIDAY for row in scenario.rows)
    assert not any(row.target_arrival_at == trial_arrival for row in scenario.rows)
    assert not used_case_keys & excluded_keys


def test_trial_trip_still_counts_as_preceding_vehicle(scenario: BacktestResult) -> None:
    # T8(08:05)의 앞차는 시운전 X(07:50)다. X 를 앞차로 쓰지 않으면 간격이 30분이 된다.
    row = row_at(scenario, TEST_DAY, time(8, 5), 5)

    assert row.headway_min == 15.0
    assert row.forecast.status == "ok"


def test_arrivals_outside_forecast_hours_are_not_targets(scenario: BacktestResult) -> None:
    arrival_times = {row.target_arrival_at.time() for row in scenario.rows}

    assert time(5, 50) not in arrival_times
    assert min(arrival_times) == time(6, 5)


# --- 방법 B --------------------------------------------------------------------
@pytest.mark.parametrize(
    ("lead_time_min", "provided", "insufficient", "tp_fn_fp_tn", "squared_error_sum"),
    [
        (5, 15, 42, (1, 1, 1, 12), 2 + 2 / 900),
        (10, 15, 42, (1, 1, 1, 12), 2 + 2 / 900),
        # 15분 간격이라 바로 앞 운행의 확정 시각이 기준 시각과 같아 사례에서 빠진다.
        (15, 14, 43, (1, 1, 1, 11), 2 + 1 / 900),
    ],
)
def test_case_method_counts_g1300(
    scenario: BacktestResult,
    lead_time_min: int,
    provided: int,
    insufficient: int,
    tp_fn_fp_tn: tuple[int, int, int, int],
    squared_error_sum: float,
) -> None:
    summary = method(scenario, lead_time_min, "G1300")

    assert summary.targets == 58
    assert summary.case_provided.total == provided
    assert summary.case_insufficient == insufficient
    assert summary.case_missing_input == 1
    assert summary.missing_reasons == {"seat_unknown": 1}
    assert counts(summary.case_provided) == tp_fn_fp_tn
    assert summary.case_provided.precision == 0.5
    assert summary.case_provided.recall == 0.5
    assert summary.case_provided.brier == pytest.approx(squared_error_sum / provided)


def test_case_method_rows_on_test_day(scenario: BacktestResult) -> None:
    def cells(arrival: time, lead: int) -> tuple[str, int | None, int | None, bool | None]:
        row = row_at(scenario, TEST_DAY, arrival, lead)
        return row.forecast.status, row.forecast.n, row.forecast.k, row.case_alert

    assert cells(time(6, 5), 5) == ("ok", 20, 20, True)  # T1 맞힌 만차
    assert cells(time(6, 20), 5) == ("ok", 21, 21, True)  # T2 잘못 알린 만차(같은 날 T1 포함)
    assert cells(time(6, 20), 15) == ("ok", 20, 20, True)  # T1 확정 시각 = 기준 시각 → 제외
    assert cells(time(6, 35), 5) == ("ok", 30, 0, False)  # T3 놓친 만차, 최대 30건
    assert cells(time(6, 50), 5) == ("ok", 30, 1, False)  # T4: 같은 날 T3(0석)이 사례
    assert cells(time(6, 50), 15) == ("ok", 30, 0, False)
    assert cells(time(7, 5), 5) == ("insufficient_cases", 0, 0, None)  # T5
    assert cells(time(7, 20), 5) == ("insufficient_cases", 1, 1, None)  # T6: T5 한 건
    assert cells(time(7, 35), 5) == ("missing_input", 0, 0, None)  # T7 잔여석 −1


def test_row_fields(scenario: BacktestResult) -> None:
    row = row_at(scenario, TEST_DAY, time(6, 50), 5)
    forecast = row.forecast

    assert forecast.issued_at == moment(TEST_DAY, time(6, 45))
    assert forecast.no_seat_probability == 0.0333  # 저장용 4자리 반올림
    assert row.case_probability == 1 / 30  # 지표는 반올림 전 값
    assert (
        forecast.case_trip_keys[0] == row_at(scenario, TEST_DAY, time(6, 35), 5).forecast.trip_key
    )
    assert len(forecast.case_trip_keys) == forecast.n
    assert (forecast.label, forecast.label_grade) == (0, "strict")
    assert (forecast.rules_version, forecast.label_rule_version) == (
        RULES_VERSION,
        LABEL_RULE_VERSION,
    )
    assert (row.reference_seats, row.headway_min, row.simple_alert) == (20, 15.0, False)


def test_cases_come_only_from_before_the_reference_time(scenario: BacktestResult) -> None:
    arrival_by_key = {s.label.trip_key: s.label.target_arrival_at for s in scenario.selections}

    for row in scenario.rows:
        for key in row.forecast.case_trip_keys:
            assert arrival_by_key[key] < row.forecast.issued_at


# --- 방법 A --------------------------------------------------------------------
@pytest.mark.parametrize("lead_time_min", [5, 10, 15])
def test_simple_method_counts_g1300(scenario: BacktestResult, lead_time_min: int) -> None:
    summary = method(scenario, lead_time_min, "G1300")

    assert summary.simple_decided.total == 57
    assert summary.simple_undecided == 1  # T7: 잔여석 −1 은 0 이 아니다
    assert counts(summary.simple_decided) == (1, 22, 1, 33)
    assert summary.simple_decided.precision == 0.5
    assert summary.simple_decided.recall == pytest.approx(1 / 23)
    assert summary.simple_decided.brier == pytest.approx(23 / 57)


@pytest.mark.parametrize(("lead_time_min", "tn"), [(5, 13), (10, 13), (15, 12)])
def test_simple_method_on_trips_where_case_method_provided(
    scenario: BacktestResult, lead_time_min: int, tn: int
) -> None:
    confusion = method(scenario, lead_time_min, "G1300").simple_on_case_provided

    assert counts(confusion) == (0, 2, 0, tn)
    assert confusion.precision is None  # 경보가 없다
    assert confusion.recall == 0.0
    assert confusion.brier == pytest.approx(2 / (2 + tn))


def test_unknown_seat_row_is_undecided_not_zero(scenario: BacktestResult) -> None:
    row = row_at(scenario, TEST_DAY, time(7, 35), 5)

    assert row.reference_seats is None
    assert row.simple_alert is None
    assert row.uncomputable_reason == "seat_unknown"


def test_simple_method_decides_when_only_headway_is_missing(scenario: BacktestResult) -> None:
    # 1306 은 앞차가 없어 방법 B 는 입력 누락이지만, 잔여석(0)은 알므로 방법 A 는 판단한다.
    summary = method(scenario, 5, "1306")

    assert summary.case_missing_input == 1
    assert summary.missing_reasons == {"no_preceding_vehicle": 1}
    assert summary.case_provided.total == 0
    assert counts(summary.simple_decided) == (1, 0, 0, 0)
    assert summary.simple_undecided == 0


# --- 합계와 참고값 ----------------------------------------------------------------
def test_total_row_is_the_sum_of_routes(scenario: BacktestResult) -> None:
    total = method(scenario, 5, "ALL")

    assert total.targets == 59
    assert total.case_provided.total == 15
    assert (total.case_insufficient, total.case_missing_input) == (42, 2)
    assert counts(total.simple_decided) == (2, 22, 1, 33)


def test_zero_rate_reference(scenario: BacktestResult) -> None:
    summary = method(scenario, 5, "G1300")

    assert summary.reference_all == ZeroRateReference(count=58, zero_count=23)
    assert summary.reference_all.brier == pytest.approx((23 / 58) * (35 / 58))
    assert summary.reference_case_provided == ZeroRateReference(count=15, zero_count=2)


def test_empty_cells_have_no_ratio() -> None:
    empty = tally([])

    assert (empty.total, empty.precision, empty.recall, empty.brier) == (0, None, None, None)
    assert ZeroRateReference(count=0, zero_count=0).brier is None


def test_report_with_no_trips_prints_zero_and_dash() -> None:
    result = evaluate_labeled_trips([])
    lines = format_report(
        info=RunInfo(
            date_from=TEST_DAY,
            date_to=TEST_DAY,
            data_dir=Path("unused"),
            grades=result.grades,
            read_days=[],
            missing_days=[],
            weekend_days=[],
            weekend_days_with_file=[],
            non_service_days=[],
            seq_mismatch_days=[],
        ),
        samples=summarize_sample(result.selections, ROUTE_NAMES),
        methods=summarize_methods(result.rows, ROUTE_NAMES, result.lead_times_min),
        rules=FORECAST_RULES,
        criteria=PUBLISH_CRITERIA,
        hours=SERVICE_HOURS,
    )

    row = next(line for line in lines if line.startswith("| 5분 | G1300 |"))
    assert (
        row
        == f"| 5분 | G1300 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | {' | '.join([NOT_AVAILABLE] * 3)} |"
    )
    assert not any("어림셈" in line for line in lines)
    assert any("기획안 정의 확인 필요" in line for line in lines)


# --- 운행편 고르기 ----------------------------------------------------------------
def _records_reaching(day: date, veh_id: str, arrival: time, last_seq: int) -> list[PositionRecord]:
    """목표(13) 직전까지 정상 기록이 있고 마지막 기록의 순번만 last_seq 인 운행편."""
    records = trip_records(day, veh_id, arrival, seats=5, last_seat=5)[:-2]
    return [*records, pos(moment(day, arrival), last_seq, 1, 5, veh_id=veh_id)]


def test_classify_trip_reasons() -> None:
    day = BASE_DAYS[0]
    saturday = date(2026, 10, 10)

    def exclusion(records: list[PositionRecord], **kwargs: object) -> Exclusion | None:
        (trip,) = build_trips(records)
        return classify_trip(label_trip(trip, 13), **kwargs)

    normal = trip_records(day, "1", time(6, 30), seats=5, last_seat=0)
    relaxed = trip_records(day, "1", time(6, 30), seats=5, last_seat=0, is_departure_recorded=False)

    assert exclusion(normal) is None
    assert exclusion(relaxed) is None
    assert exclusion(relaxed, grades=(Grade.STRICT,)) is Exclusion.GRADE_NOT_USED
    assert exclusion(trip_records(day, "1", time(6, 30), seats=5, last_seat=-1)) is (
        Exclusion.UNCONFIRMED
    )
    assert exclusion(normal[:-2]) is Exclusion.NO_TARGET_ARRIVAL
    assert exclusion(trip_records(day, "1", time(5, 59), seats=5, last_seat=0)) is (
        Exclusion.OUTSIDE_FORECAST_HOURS
    )
    assert exclusion(trip_records(day, "1", time(9, 0), seats=5, last_seat=0)) is (
        Exclusion.OUTSIDE_FORECAST_HOURS
    )
    assert exclusion(trip_records(day, "1", time(8, 59), seats=5, last_seat=0)) is None
    assert exclusion(
        trip_records(day, "1", time(6, 30), seats=5, last_seat=0, mode=MODE_TRIAL)
    ) is (Exclusion.TRIAL)
    assert exclusion(
        trip_records(day, "1", time(6, 30), seats=5, last_seat=0, is_holiday=True)
    ) is (Exclusion.NON_SERVICE_DAY)
    assert exclusion(trip_records(HOLIDAY, "1", time(6, 30), seats=5, last_seat=0)) is (
        Exclusion.NON_SERVICE_DAY
    )
    assert exclusion(trip_records(saturday, "1", time(6, 30), seats=5, last_seat=0)) is (
        Exclusion.NON_SERVICE_DAY
    )


def test_unconfirmed_reasons_are_counted_in_sample() -> None:
    day = BASE_DAYS[0]
    records = [
        *trip_records(day, "1", time(6, 30), seats=5, last_seat=-1),  # seat_unknown
        *_records_reaching(day, "2", time(6, 45), 12),  # 목표에 닿지 않음
    ]
    result = run_backtest(build_trips(records), TARGET_STOPS)
    g1300 = summarize_sample(result.selections, ROUTE_NAMES)[0]

    assert result.rows == ()
    assert g1300.by_exclusion == {Exclusion.UNCONFIRMED: 1, Exclusion.NO_TARGET_ARRIVAL: 1}
    assert g1300.unconfirmed_reasons == {UnconfirmedReason.SEAT_UNKNOWN: 1}
    assert (g1300.targets, g1300.in_hours_arrivals) == (0, 1)


def test_strict_only_drops_relaxed_from_cases_and_targets() -> None:
    # 사례 1건이면 확률을 내도록 표시 하한만 낮춘 규칙(검색 로직 확인용. 고정 규칙 값은 그대로다).
    rules = ForecastRules(min_cases_to_show=1)
    first, second = BASE_DAYS[0], BASE_DAYS[1]
    records = [
        *trip_records(first, "1", time(5, 50), seats=5, last_seat=0),
        *trip_records(first, "2", time(6, 5), seats=5, last_seat=0),
        *trip_records(first, "3", time(6, 20), seats=5, last_seat=0, is_departure_recorded=False),
        *trip_records(second, "4", time(5, 50), seats=5, last_seat=0),
        *trip_records(second, "5", time(6, 5), seats=5, last_seat=3),
    ]
    trips = build_trips(records)

    both = run_backtest(trips, TARGET_STOPS, rules=rules)
    strict_only = run_backtest(trips, TARGET_STOPS, rules=rules, grades=(Grade.STRICT,))

    def last_row(result: BacktestResult) -> BacktestRow:
        return next(
            row
            for row in result.rows
            if row.forecast.service_date == second and row.forecast.lead_time_min == 5
        )

    assert (last_row(both).forecast.n, last_row(both).forecast.k) == (2, 2)
    assert (last_row(strict_only).forecast.n, last_row(strict_only).forecast.k) == (1, 1)
    assert len(both.rows) == 3 * 3
    assert len(strict_only.rows) == 2 * 3
    assert Exclusion.GRADE_NOT_USED in {s.exclusion for s in strict_only.selections}
    assert {row.forecast.label_grade for row in both.rows} == {"strict", "relaxed"}


def test_label_trips_one_day_at_a_time_gives_the_same_result() -> None:
    by_day = build_scenario_records()
    all_at_once = label_trips(build_trips([r for rs in by_day.values() for r in rs]), TARGET_STOPS)
    day_by_day = [
        labeled for rs in by_day.values() for labeled in label_trips(build_trips(rs), TARGET_STOPS)
    ]

    assert day_by_day == all_at_once


def test_trips_of_other_routes_are_ignored() -> None:
    records = trip_records(BASE_DAYS[0], "1", time(6, 30), seats=5, last_seat=0)
    other_route = [
        pos(r.collected_at, r.station_seq, r.state_cd, r.remain_seat_cnt, route_id="235000085")
        for r in records
    ]

    assert label_trips(build_trips(other_route), TARGET_STOPS) == []
