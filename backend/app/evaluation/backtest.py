"""백테스트 핵심 계산(순수 함수. 파일·DB·시계를 읽지 않는다).

입력은 운행편(Trip) 목록과 목표 정류장, 규칙이다. JSONL 에서 읽은 기록이든 나중에 DB 에서 읽은
기록이든 Trip 으로 만들어 넘기면 같은 결과가 나온다.

두 단계로 나눈다. 여러 날을 돌릴 때 위치 기록을 모두 메모리에 들고 있지 않으려는 것이다.
1. label_trips: 운행편마다 정답 라벨과 선행시간별 사례 특징을 계산한다. 앞차 간격은 같은 날
   운행편끼리만 보므로 하루치씩 불러 계산해도 결과가 같다. 결과에는 위치 기록이 없다.
2. evaluate_labeled_trips: 평가 대상 운행편 × 선행시간마다 사례를 검색하고 두 방법의 판단을 낸다.
run_backtest 는 둘을 이어 부른다.

운행편을 고르는 순서(앞에서 걸리면 뒤는 보지 않는다. Exclusion 참조):
시운전 → 예보하지 않는 날(공휴일·주말) → 목표 정류장 도착 기록 없음 → 도착이 예보 시간대 밖
→ 정답 미확인 → 쓰지 않는 정답 등급 → 평가 대상.
사례(history)는 시운전·예보하지 않는 날이 아니고 정답이 확정된(쓰는 등급의) 운행편 전부다.
사례에는 도착 시간대 조건을 두지 않는다(CLAUDE.md 사례 필터에 시간대가 없다).
"""

from collections import defaultdict
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.core.settings import (
    COLLECT_WINDOW,
    FORECAST_RULES,
    LABEL_RULE_VERSION,
    PUBLISH_CRITERIA,
    RULES_VERSION,
    SERVICE_HOURS,
    CollectWindow,
    ForecastRules,
    PublishCriteria,
    ServiceHours,
)
from app.forecast.case_features import compute_case_features
from app.forecast.case_search import search_cases
from app.forecast.interfaces import (
    CaseFeature,
    CaseSearchResult,
    EvalForecast,
    LabeledCase,
    UncomputableReason,
)
from app.forecast.risk import no_seat_probability
from app.forecast.service_time import in_forecast_hours, is_service_day
from app.labeling.labels import Grade, TripLabel, label_trip
from app.labeling.targets import TargetStop
from app.labeling.trips import Trip

# 정답으로 쓰는 등급의 기본값. strict 만 쓰려면 grades=(Grade.STRICT,) 를 넘긴다.
DEFAULT_GRADES: tuple[Grade, ...] = (Grade.STRICT, Grade.RELAXED)


class Exclusion(StrEnum):
    """운행편을 평가 대상에서 뺀 사유. 위에서부터 확인하고 처음 걸린 것 하나만 남긴다."""

    TRIAL = "trial"  # 시운전(mode=trial) 기록이 섞인 운행편
    NON_SERVICE_DAY = "non_service_day"  # 공휴일(수집기 표시 또는 설정 목록)·주말
    NO_TARGET_ARRIVAL = "no_target_arrival"  # 목표 정류장 도착 기록 없음(도착 시각을 모른다)
    OUTSIDE_FORECAST_HOURS = "outside_forecast_hours"  # 도착이 예보 대상 시간대 밖
    UNCONFIRMED = "unconfirmed"  # 정답 미확인(사유는 TripLabel.reason)
    GRADE_NOT_USED = "grade_not_used"  # 정답은 있지만 이번 실행에서 쓰지 않는 등급


@dataclass(frozen=True)
class LabeledTrip:
    """운행편 하나의 정답 라벨과 선행시간별 사례 특징. 위치 기록은 들고 있지 않다."""

    label: TripLabel
    station_id: str  # 목표 정류장
    features: tuple[CaseFeature, ...]  # rules.lead_times_min 순서


@dataclass(frozen=True)
class TripSelection:
    """운행편 하나가 평가 대상인지와 빠졌다면 그 사유(표본 집계용)."""

    label: TripLabel
    exclusion: Exclusion | None  # None 이면 평가 대상


@dataclass(frozen=True)
class BacktestRow:
    """평가 대상 운행편 × 선행시간 1행: 방법 B 예보(EvalForecast)와 방법 A 판단을 나란히 둔다."""

    forecast: EvalForecast  # 방법 B. eval_forecast 한 행과 같은 모양
    target_arrival_at: datetime  # 목표 정류장 도착(정답 확정) 시각
    reference_seats: int | None  # 기준 시각 잔여석(방법 A 의 입력). 모르면 None
    headway_min: float | None  # 기준 시각 앞차 간격(분)
    uncomputable_reason: UncomputableReason | None  # 특징을 계산하지 못한 사유
    # 방법 A: 기준 시각 잔여석이 0 이면 True, 1 이상이면 False, 잔여석을 모르면 None(판단 불가).
    simple_alert: bool | None
    # 방법 B: 확률이 경보 기준 이상이면 True, 미만이면 False, 확률을 내지 못했으면 None(보류).
    case_alert: bool | None

    @property
    def case_probability(self) -> float | None:
        """반올림하지 않은 k ÷ n. 방법 B 가 확률을 냈을 때(status ok)만."""
        forecast = self.forecast
        if forecast.status != "ok" or not forecast.n or forecast.k is None:
            return None
        return forecast.k / forecast.n


@dataclass(frozen=True)
class BacktestResult:
    rows: tuple[BacktestRow, ...]  # 평가 대상 운행편 × 선행시간
    selections: tuple[TripSelection, ...]  # 목표 노선의 모든 운행편
    lead_times_min: tuple[int, ...]
    grades: tuple[Grade, ...]


def label_trips(
    trips: Sequence[Trip],
    target_stops: Mapping[str, TargetStop],
    *,
    rules: ForecastRules = FORECAST_RULES,
) -> list[LabeledTrip]:
    """운행편마다 정답 라벨과 선행시간별 사례 특징(actual_arrival 기준)을 계산한다.

    - trips: 하루 또는 여러 날의 운행편. 앞차 간격 때문에 한 날짜·노선의 운행편은 모두 함께
      넘겨야 한다(시운전·공휴일 운행편 포함). 날짜가 다른 운행편은 서로 영향을 주지 않는다.
    - target_stops: routeId → 목표 정류장. 여기에 없는 노선의 운행편은 결과에서 뺀다.
    결과 순서는 입력 순서와 같다.
    """
    trips_by_day_route: dict[tuple[date, str], list[Trip]] = defaultdict(list)
    for trip in trips:
        if trip.route_id in target_stops:
            trips_by_day_route[(trip.service_date, trip.route_id)].append(trip)

    labeled: list[LabeledTrip] = []
    for trip in trips:
        stop = target_stops.get(trip.route_id)
        if stop is None:
            continue
        day_trips = trips_by_day_route[(trip.service_date, trip.route_id)]
        features = tuple(
            compute_case_features(trip, stop, lead, "actual_arrival", day_trips=day_trips)
            for lead in rules.lead_times_min
        )
        labeled.append(
            LabeledTrip(
                label=label_trip(trip, stop.station_seq),
                station_id=stop.station_id,
                features=features,
            )
        )
    return labeled


def _is_usable_day(label: TripLabel, window: CollectWindow, hours: ServiceHours) -> bool:
    """사례·평가에 쓰는 날의 운행편인가: 시운전이 아니고 예보하는 날(평일, 공휴일 아님)이다."""
    return (
        not label.is_trial
        and not label.is_holiday
        and is_service_day(label.service_date, window, hours)
    )


def _has_usable_label(label: TripLabel, grades: Collection[Grade]) -> bool:
    return label.is_confirmed and label.grade in grades


def classify_trip(
    label: TripLabel,
    *,
    grades: Collection[Grade] = DEFAULT_GRADES,
    hours: ServiceHours = SERVICE_HOURS,
    window: CollectWindow = COLLECT_WINDOW,
) -> Exclusion | None:
    """운행편이 평가 대상이면 None, 아니면 뺀 사유(Exclusion 의 정의 순서로 확인)."""
    if label.is_trial:
        return Exclusion.TRIAL
    if not _is_usable_day(label, window, hours):
        return Exclusion.NON_SERVICE_DAY
    if label.target_arrival_at is None:
        return Exclusion.NO_TARGET_ARRIVAL
    if not in_forecast_hours(label.target_arrival_at, hours):
        return Exclusion.OUTSIDE_FORECAST_HOURS
    if not label.is_confirmed:
        return Exclusion.UNCONFIRMED
    if label.grade not in grades:
        return Exclusion.GRADE_NOT_USED
    return None


def _build_row(
    labeled_trip: LabeledTrip,
    feature: CaseFeature,
    result: CaseSearchResult,
    target_arrival_at: datetime,
    criteria: PublishCriteria,
) -> BacktestRow:
    label = labeled_trip.label
    exact_probability = result.no_seat_probability  # status ok 일 때만 값이 있다
    stored_probability: float | None = None
    case_alert: bool | None = None
    if exact_probability is not None:
        stored_probability, _ = no_seat_probability(result.k, result.n)
        # 경보 여부는 반올림 전 k ÷ n 으로 정한다(위험 등급과 같은 방식).
        case_alert = exact_probability >= criteria.alert_probability_min
    return BacktestRow(
        forecast=EvalForecast(
            trip_key=feature.trip_key,
            service_date=feature.service_date,
            route_id=feature.route_id,
            station_id=feature.station_id,
            lead_time_min=feature.lead_time_min,
            issued_at=feature.reference_at,
            status=result.status,
            n=result.n,
            k=result.k,
            no_seat_probability=stored_probability,
            case_trip_keys=result.case_trip_keys,
            label=label.label,
            label_grade=label.grade.value,
            rules_version=RULES_VERSION,
            label_rule_version=LABEL_RULE_VERSION,
        ),
        target_arrival_at=target_arrival_at,
        reference_seats=feature.seats,
        headway_min=feature.headway_min,
        uncomputable_reason=feature.uncomputable_reason,
        # 잔여석 0 과 '모름'(None)을 섞지 않는다. 모르면 판단하지 않는다.
        simple_alert=None if feature.seats is None else feature.seats == 0,
        case_alert=case_alert,
    )


def evaluate_labeled_trips(
    labeled_trips: Sequence[LabeledTrip],
    *,
    grades: Collection[Grade] = DEFAULT_GRADES,
    rules: ForecastRules = FORECAST_RULES,
    criteria: PublishCriteria = PUBLISH_CRITERIA,
    hours: ServiceHours = SERVICE_HOURS,
    window: CollectWindow = COLLECT_WINDOW,
) -> BacktestResult:
    """평가 대상 운행편 × 선행시간마다 사례 검색(방법 B)과 단순 기준(방법 A) 판단을 낸다.

    - labeled_trips: label_trips 의 결과(기간 전체). 순서는 결과에 영향을 주지 않는다.
    - grades: 정답으로 쓰는 등급. 사례와 평가 대상 모두에 적용한다.
    - 사례 후보는 기간 전체에서 만들고, '예보 시점 전에 확정된 운행만'은 search_cases 가 거른다.
    한 운행편은 선행시간마다 1행만 낸다(조회 시점마다 중복해 세지 않는다).
    """
    grades = tuple(grades)
    selections = [
        TripSelection(lt.label, classify_trip(lt.label, grades=grades, hours=hours, window=window))
        for lt in labeled_trips
    ]

    # 노선·목표 정류장·선행시간이 같은 사례끼리 미리 묶는다. search_cases 가 같은 조건으로
    # 다시 거르므로 결과는 전체를 넘길 때와 같고, 검색마다 전체를 훑지 않게 하려는 것이다.
    history: dict[tuple[str, str, int], list[LabeledCase]] = defaultdict(list)
    for lt in labeled_trips:
        if not (_is_usable_day(lt.label, window, hours) and _has_usable_label(lt.label, grades)):
            continue
        for feature in lt.features:
            history[(feature.route_id, feature.station_id, feature.lead_time_min)].append(
                LabeledCase(
                    feature=feature,
                    label=lt.label.label,
                    label_grade=lt.label.grade.value,
                    label_rule_version=LABEL_RULE_VERSION,
                    confirmed_at=lt.label.target_arrival_at,
                )
            )

    rows: list[BacktestRow] = []
    for lt, selection in zip(labeled_trips, selections, strict=True):
        arrival_at = lt.label.target_arrival_at
        if selection.exclusion is not None or arrival_at is None:
            continue
        for feature in lt.features:
            candidates = history.get(
                (feature.route_id, feature.station_id, feature.lead_time_min), ()
            )
            result = search_cases(feature, candidates, rules)
            rows.append(_build_row(lt, feature, result, arrival_at, criteria))

    return BacktestResult(
        rows=tuple(rows),
        selections=tuple(selections),
        lead_times_min=rules.lead_times_min,
        grades=grades,
    )


def run_backtest(
    trips: Sequence[Trip],
    target_stops: Mapping[str, TargetStop],
    *,
    grades: Collection[Grade] = DEFAULT_GRADES,
    rules: ForecastRules = FORECAST_RULES,
    criteria: PublishCriteria = PUBLISH_CRITERIA,
    hours: ServiceHours = SERVICE_HOURS,
    window: CollectWindow = COLLECT_WINDOW,
) -> BacktestResult:
    """운행편 목록(날짜순, 기간 전체)으로 백테스트를 한 번에 돌린다.

    label_trips → evaluate_labeled_trips 와 같다. 입력·규칙은 두 함수의 설명을 따른다.
    """
    return evaluate_labeled_trips(
        label_trips(trips, target_stops, rules=rules),
        grades=grades,
        rules=rules,
        criteria=criteria,
        hours=hours,
        window=window,
    )
