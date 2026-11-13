"""백테스트 집계: 표본 수, 두 방법의 맞힘·놓침 건수, 정밀도·재현율, Brier 점수.

분모가 0 인 비율은 None 이다(표에서는 '-'). 0 으로 바꾸지 않는다.
공개 판정(pass/fail)은 하지 않는다. 숫자만 낸다.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field

from app.evaluation.backtest import BacktestRow, Exclusion, TripSelection
from app.forecast.interfaces import UncomputableReason
from app.labeling.labels import Grade, UnconfirmedReason
from app.labeling.report import TOTAL_ROUTE_NAME


def _ratio(numerator: float, denominator: int) -> float | None:
    return numerator / denominator if denominator > 0 else None


@dataclass(frozen=True)
class Confusion:
    """경보 판단과 정답(도착 상태 0석)의 2×2 집계와 Brier 점수의 재료."""

    tp: int = 0  # 맞힌 만차: 경보했고 실제 0석
    fn: int = 0  # 놓친 만차: 경보하지 않았는데 실제 0석
    fp: int = 0  # 잘못 알린 만차: 경보했는데 0석 아님
    tn: int = 0  # 맞힌 비만차: 경보하지 않았고 0석 아님
    squared_error_sum: float = 0.0  # (예측 확률 − 정답)² 의 합

    @property
    def total(self) -> int:
        return self.tp + self.fn + self.fp + self.tn

    @property
    def precision(self) -> float | None:
        """경보한 것 중 실제 0석 비율. 경보가 없으면 None."""
        return _ratio(self.tp, self.tp + self.fp)

    @property
    def recall(self) -> float | None:
        """실제 0석 중 경보한 비율. 실제 0석이 없으면 None."""
        return _ratio(self.tp, self.tp + self.fn)

    @property
    def brier(self) -> float | None:
        """(예측 확률 − 정답)² 의 평균. 건수가 0 이면 None."""
        return _ratio(self.squared_error_sum, self.total)


def tally(judgments: Iterable[tuple[bool, float, int]]) -> Confusion:
    """(경보 여부, 예측 확률, 정답 0·1) 목록을 Confusion 으로 센다."""
    tp = fn = fp = tn = 0
    squared_error_sum = 0.0
    for is_alert, probability, label in judgments:
        if label == 1:
            tp, fn = tp + int(is_alert), fn + int(not is_alert)
        else:
            fp, tn = fp + int(is_alert), tn + int(not is_alert)
        squared_error_sum += (probability - label) ** 2
    return Confusion(tp=tp, fn=fn, fp=fp, tn=tn, squared_error_sum=squared_error_sum)


@dataclass(frozen=True)
class ZeroRateReference:
    """참고값: 대상의 0석 도착 비율을 모든 운행편의 예측으로 그대로 썼을 때의 Brier.

    비율을 평가 대상 자신의 정답으로 계산하므로(표본 내) 예보 시점에는 알 수 없는 값이다.
    기획안의 '어림셈 기준선'이 이 정의인지는 확인되지 않았다.
    """

    count: int
    zero_count: int

    @property
    def zero_rate(self) -> float | None:
        return _ratio(self.zero_count, self.count)

    @property
    def brier(self) -> float | None:
        rate = self.zero_rate
        # 예측이 r 로 같으면 (r − 1)² 이 zero_count 번, r² 이 나머지 번이라 평균은 r(1 − r) 이다.
        return None if rate is None else rate * (1 - rate)


@dataclass(frozen=True)
class MethodSummary:
    """선행시간 × 노선 한 칸의 두 방법 비교."""

    lead_time_min: int
    route_name: str
    targets: int  # 평가 대상 운행편 수(이 선행시간의 행 수)
    # 방법 B(사례 방식)
    case_provided: Confusion  # 확률을 낸(status ok) 운행편
    case_insufficient: int  # 보류: 사례 부족
    case_missing_input: int  # 보류: 입력 누락
    missing_reasons: Mapping[UncomputableReason, int]
    # 방법 A(단순 기준)
    simple_decided: Confusion  # (a) 기준 시각 잔여석을 아는 전체 운행편
    simple_undecided: int  # 잔여석을 몰라 판단하지 못한 운행편
    simple_on_case_provided: Confusion  # (b) 방법 B 가 확률을 낸 같은 운행편만
    # 참고
    reference_all: ZeroRateReference
    reference_case_provided: ZeroRateReference


def _simple_judgments(rows: Iterable[BacktestRow]) -> list[tuple[bool, float, int]]:
    """방법 A 의 판단. 0/1 판단을 그대로 확률(1.0 또는 0.0)로 본다. 판단 불가·정답 없음은 뺀다."""
    return [
        (row.simple_alert, float(row.simple_alert), row.forecast.label)
        for row in rows
        if row.simple_alert is not None and row.forecast.label is not None
    ]


def _zero_rate(rows: Sequence[BacktestRow]) -> ZeroRateReference:
    labels = [row.forecast.label for row in rows if row.forecast.label is not None]
    return ZeroRateReference(count=len(labels), zero_count=sum(labels))


def _summarize_cell(
    lead_time_min: int, route_name: str, rows: Sequence[BacktestRow]
) -> MethodSummary:
    provided = [row for row in rows if row.forecast.status == "ok"]
    missing = [row for row in rows if row.forecast.status == "missing_input"]
    case_judgments = [
        (row.case_alert, row.case_probability, row.forecast.label)
        for row in provided
        if row.case_alert is not None
        and row.case_probability is not None
        and row.forecast.label is not None
    ]
    return MethodSummary(
        lead_time_min=lead_time_min,
        route_name=route_name,
        targets=len(rows),
        case_provided=tally(case_judgments),
        case_insufficient=sum(1 for row in rows if row.forecast.status == "insufficient_cases"),
        case_missing_input=len(missing),
        missing_reasons=Counter(
            row.uncomputable_reason for row in missing if row.uncomputable_reason is not None
        ),
        simple_decided=tally(_simple_judgments(rows)),
        simple_undecided=sum(1 for row in rows if row.simple_alert is None),
        simple_on_case_provided=tally(_simple_judgments(provided)),
        reference_all=_zero_rate(rows),
        reference_case_provided=_zero_rate(provided),
    )


def summarize_methods(
    rows: Sequence[BacktestRow],
    route_names: Mapping[str, str],
    lead_times_min: Sequence[int],
) -> list[MethodSummary]:
    """선행시간마다 route_names 순서의 노선별 집계와 노선 합계(ALL).

    행이 없는 칸도 0 으로 넣는다.
    """
    summaries: list[MethodSummary] = []
    for lead in lead_times_min:
        lead_rows = [row for row in rows if row.forecast.lead_time_min == lead]
        for route_id, route_name in route_names.items():
            route_rows = [row for row in lead_rows if row.forecast.route_id == route_id]
            summaries.append(_summarize_cell(lead, route_name, route_rows))
        summaries.append(_summarize_cell(lead, TOTAL_ROUTE_NAME, lead_rows))
    return summaries


@dataclass
class SampleSummary:
    """노선 하나(또는 합계)의 표본: 운행편이 어디서 얼마나 빠졌고 몇 건이 평가 대상인가."""

    route_name: str
    trips: int = 0  # 목표 노선의 전체 운행편
    by_exclusion: Counter[Exclusion] = field(default_factory=Counter)
    # 정답 미확인으로 뺀 운행편(Exclusion.UNCONFIRMED)의 사유별 수
    unconfirmed_reasons: Counter[UnconfirmedReason] = field(default_factory=Counter)
    targets: int = 0  # 평가 대상 운행편
    zero_arrivals: int = 0  # 평가 대상 중 실제 0석 도착(label = 1)
    by_grade: Counter[Grade] = field(default_factory=Counter)  # 평가 대상의 정답 등급

    @property
    def in_hours_arrivals(self) -> int:
        """예보 시간대에 목표 정류장에 도착한 운행편(정답 미확인 비율의 분모)."""
        return (
            self.targets
            + self.by_exclusion[Exclusion.UNCONFIRMED]
            + self.by_exclusion[Exclusion.GRADE_NOT_USED]
        )

    def add(self, selection: TripSelection) -> None:
        label = selection.label
        self.trips += 1
        if selection.exclusion is None:
            self.targets += 1
            self.zero_arrivals += int(label.label == 1)
            self.by_grade[label.grade] += 1
            return
        self.by_exclusion[selection.exclusion] += 1
        if selection.exclusion is Exclusion.UNCONFIRMED and label.reason is not None:
            self.unconfirmed_reasons[label.reason] += 1


def summarize_sample(
    selections: Iterable[TripSelection], route_names: Mapping[str, str]
) -> list[SampleSummary]:
    """route_names 순서의 노선별 표본과 마지막에 합계(ALL). 운행편이 없는 노선도 0 으로 넣는다."""
    by_route = {route_id: SampleSummary(name) for route_id, name in route_names.items()}
    total = SampleSummary(TOTAL_ROUTE_NAME)
    for selection in selections:
        route_id = selection.label.route_id
        summary = by_route.get(route_id)
        if summary is None:
            summary = by_route[route_id] = SampleSummary(route_id)
        summary.add(selection)
        total.add(selection)
    return [*by_route.values(), total]
