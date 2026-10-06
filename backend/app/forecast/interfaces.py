"""모델링 담당자와 합의할 계산 함수의 입출력(회의용 초안, 본문 미구현).

사례 검색·예보·구간 소요시간 계산의 시그니처와 데이터 모양만 정한다. 구현은 합의 후에 한다.
같은 내용을 docs/compute-interface.md 에 표로 정리했다.

이름 기준
- 계산 함수의 입출력 필드 이름은 DB 스키마 v2(case_feature, eval_forecast, forecast_snapshot,
  forecast_group, trip_label, publish_decision)의 열 이름(snake_case)을 따른다.
- 스키마 v2 는 다른 작업자가 같은 시기에 고치고 있어(별도 PR) 열 이름이 바뀔 수 있다.
  다르면 스키마 쪽 이름으로 맞춘다.
- API 응답 필드 이름(selectedLeadTimeMin, vehicleId 등)은 계약 그대로이며, DB·계산 이름과의
  변환은 저장소 계층에서 한다. 대응표는 docs/compute-interface.md.

고정 규칙(CLAUDE.md '바꾸지 않는 규칙')의 숫자는 settings 의 ForecastRules·ArrivalRules 로만
넘긴다. 함수 안에 숫자를 다시 쓰지 않는다.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal

from app.core.settings import CASE_FEATURE_RULE_VERSION, ArrivalRules, ForecastRules
from app.labeling.targets import TargetStop
from app.labeling.trips import Trip
from app.schemas.common import ArrivalEstimateSource, BusSource, ForecastStatus

# case_feature.reference_basis: '도착 L분 전' 시점을 무엇으로 잡는가.
# - actual_arrival: 지난 운행(사례). 실제 도착 기록 시각 − L분.
# - predicted_arrival: 지금 운행(예보). 데이터 시각 + 도착 예상 − L분.
ReferenceBasis = Literal["actual_arrival", "predicted_arrival"]

# 사례 검색 결과 상태. eval_forecast·forecast_snapshot 의 status 중 사례 검색이 정하는 값.
CaseSearchStatus = Literal["ok", "insufficient_cases", "missing_input"]

# publish_decision.decision_status. 행이 없거나 pending 이면 판정 전(preliminary=True),
# passed 이면 preliminary=False, failed 이면 status not_validated.
DecisionStatus = Literal["pending", "passed", "failed"]

# trip_label.grade
LabelGrade = Literal["strict", "relaxed", "unconfirmed"]

# case_feature.uncomputable_reason(스키마 v2 CHECK 값, 초안 코드로 회의에서 확정)
# - no_target_arrival: 목표 정류장 도착 기록 없음
# - no_arrival_estimate: 도착 예상 없음
# - no_record_near_reference: 기준 시각 근처 위치 기록 없음
# - seat_unknown: 잔여석 −1·없음
# - no_preceding_vehicle: 앞차 없음
UncomputableReason = Literal[
    "no_target_arrival",
    "no_arrival_estimate",
    "no_record_near_reference",
    "seat_unknown",
    "no_preceding_vehicle",
]


@dataclass(frozen=True)
class CaseFeature:
    """운행편 하나의 선행시간 L분 시점 특징(case_feature 한 행)."""

    trip_key: str  # trip.trip_key. 예: 2026-10-07_235000092_235000359_1
    service_date: date
    route_id: str
    station_id: str  # 목표(내) 정류장
    lead_time_min: int  # settings.FORECAST_RULES.lead_times_min 중 하나
    reference_at: datetime | None  # 도착 L분 전 시점(KST). 도착 시각을 정할 수 없으면 None
    reference_basis: ReferenceBasis
    seats: int | None  # 그 시점 잔여석. -1·없음이면 None
    headway_min: float | None  # 그 시점 앞차 간격(분)
    # reference_at·seats·headway_min 을 모두 정했으면 True
    computable: bool
    uncomputable_reason: UncomputableReason | None  # computable 이 False 일 때만
    rule_version: str  # 특징 계산 규칙 버전(settings.CASE_FEATURE_RULE_VERSION)


@dataclass(frozen=True)
class LabeledCase:
    """사례 후보: case_feature 와 그 운행편의 trip_label 을 이은 것.

    시운전·공휴일 운행(trip.is_trial·is_holiday)은 호출하는 쪽이 미리 뺀다.
    """

    feature: CaseFeature
    label: int | None  # trip_label.label. 1 = 도착 상태 0석, 0 = 아님, None = 미확인
    label_grade: LabelGrade
    label_rule_version: str
    confirmed_at: datetime | None  # trip_label.confirmed_at(정답 확정 = 도착 기록 시각)


@dataclass(frozen=True)
class CaseSearchResult:
    """사례 검색 결과. eval_forecast·forecast_snapshot 의 n·k·case_trip_keys·status."""

    n: int
    k: int
    case_trip_keys: tuple[str, ...]  # 가까운 순
    status: CaseSearchStatus

    @property
    def no_seat_probability(self) -> float | None:
        """k ÷ n(보정 없음). status 가 ok 일 때만."""
        return self.k / self.n if self.status == "ok" and self.n > 0 else None


@dataclass(frozen=True)
class BusState:
    """예보할 버스 한 대의 지금 상태(수집 데이터에서 서버가 계산).

    필드 이름은 forecast_snapshot 의 버스 열과 같다.
    """

    route_id: str
    veh_id: str | None
    station_id: str  # 목표(내) 정류장
    source: BusSource  # arrival_1st | arrival_2nd | timetable_next
    station_arrival_at: datetime | None
    arrival_estimate_source: ArrivalEstimateSource | None
    current_seats: int | None
    seats_updated_at: datetime | None  # stale 판정 근거
    trip: Trip | None  # 운행편을 찾았으면 그 위치 기록 묶음(특징 계산 입력)


@dataclass(frozen=True)
class Forecast:
    """선행시간 하나의 예보. forecast_snapshot 의 선행시간 열(버스 × 선행시간 3행 중 1행)."""

    lead_time_min: int
    status: ForecastStatus
    issued_at: datetime | None  # 버스가 도착 L분 전이 된 시각. not_yet 이면 None
    no_seat_probability: float | None
    n: int | None
    k: int | None
    preliminary: bool
    input_seats: int | None
    input_headway_min: float | None
    is_selected: bool  # 그 버스에서 화면에 쓸 선행시간이면 True(버스당 최대 1행)
    case_trip_keys: tuple[str, ...]


@dataclass(frozen=True)
class EvalForecast:
    """백테스트 예보 한 건(eval_forecast 한 행). 검증 지표(F10)의 입력."""

    trip_key: str
    service_date: date
    route_id: str
    station_id: str
    lead_time_min: int
    issued_at: datetime | None  # case_feature.reference_at. 기준 시각이 없으면 None
    status: ForecastStatus
    n: int | None
    k: int | None
    no_seat_probability: float | None
    case_trip_keys: tuple[str, ...]
    label: int | None
    label_grade: LabelGrade | None
    rules_version: str
    label_rule_version: str


@dataclass(frozen=True)
class SegmentTimeRecord:
    """구간 소요시간 한 건(segment_time 한 행에서 계산에 쓰는 열)."""

    route_id: str
    service_date: date
    time_bin: str  # "HH:MM", 출발 시각의 KST 30분대
    from_seq: int
    to_seq: int
    duration_sec: int
    is_trial: bool
    is_holiday: bool


def compute_case_features(
    trip: Trip,
    target_station: TargetStop,
    lead_time_min: int,
    reference_basis: ReferenceBasis,
    rule_version: str = CASE_FEATURE_RULE_VERSION,
) -> CaseFeature:
    """운행편 trip 이 target_station 에 도착하기 lead_time_min 분 전 시점의 특징.

    입력
    - trip: app.labeling.trips.Trip(같은 날·노선·차량의 위치 기록 묶음)
    - target_station: 목표 정류장과 그 노선 순번(app.labeling.targets.TargetStop)
    - lead_time_min: settings.FORECAST_RULES.lead_times_min 중 하나
    - reference_basis: 시점 기준(ReferenceBasis)
    - rule_version: 특징 계산 규칙 버전. API rulesVersion 과 따로 둔다

    출력: CaseFeature. 기준 시각·잔여석·앞차 간격 중 하나라도 정할 수 없으면
    computable=False 와 uncomputable_reason(UncomputableReason, missing_input 의 근거).

    합의할 것: 앞차 간격 계산 방법(같은 노선 앞차가 같은 정류장을 지난 시각과의 차),
    '그 시점'의 기록을 고르는 방법(그 시점 이전 마지막 기록 등).
    """
    raise NotImplementedError


def search_cases(
    feature: CaseFeature,
    history: Sequence[LabeledCase],
    rules: ForecastRules,
) -> CaseSearchResult:
    """feature 와 비슷한 지난 사례를 찾는다(CLAUDE.md '사례 검색과 확률').

    - 필터: 노선·목표 정류장·선행시간 일치, confirmed_at < feature.reference_at
      (예보 시점 전에 정답이 확정된 운행만, 같은 날 앞선 운행 포함), 정답 미확인 제외
    - 허용폭: 잔여석 ±rules.seat_tolerance, 앞차 간격 ±rules.headway_tolerance_min
    - 거리: |잔여석 차| ÷ rules.seat_distance_divisor
      + |간격 차| ÷ rules.headway_distance_divisor
    - 선택: 가까운 순 최대 rules.max_cases 건, 운행당 1건, 거리가 같으면 최근 운행 우선
    - n < rules.min_cases_to_show 이면 status insufficient_cases
    - feature.computable 이 False 이면 status missing_input, n = k = 0
    - 정답으로 쓸 등급(strict 만 / relaxed 포함)은 회의 안건

    출력: CaseSearchResult(n, k, case_trip_keys, status).
    """
    raise NotImplementedError


def forecast_bus(
    bus_state: BusState,
    now: datetime,
    rules: ForecastRules,
    *,
    history: Sequence[LabeledCase] = (),
    publish_decisions: Mapping[int, DecisionStatus] | None = None,
) -> list[Forecast]:
    """버스 한 대의 선행시간별 예보(rules.lead_times_min 각각 1개, 항상 같은 개수).

    - 도착 L분 전 시점이 아직 오지 않았으면 status not_yet, issued_at None
    - 도착이 예보 대상 시간대 밖이면 status outside_hours
    - 입력 기록이 오래됐으면 status stale(기준 settings.STALE_AFTER_SEC)
    - 그 밖에는 compute_case_features(predicted_arrival) → search_cases 결과를 담는다
    - publish_decisions[L]: 없음·pending → preliminary True, passed → preliminary False,
      failed → status not_validated
    - publish_decisions 는 호출하는 쪽이 이 버스의 노선·목표 정류장과 현재 RULES_VERSION 으로
      이미 거른 값이다(키는 선행시간). 이 함수는 노선·정류장·버전을 다시 확인하지 않는다.
    - is_selected 는 forecast.lead_time.select_lead_time 결과인 행 하나만 True

    같은 버스·선행시간의 예보는 한 번 낸 뒤 고정한다(issued_at). 이미 낸 예보를 다시 계산하지
    않도록 호출하는 쪽이 저장된 값을 먼저 찾는다.
    """
    raise NotImplementedError


def segment_p90(
    route: str,
    time_bin: str,
    history: Sequence[SegmentTimeRecord],
    rules: ArrivalRules | None = None,
) -> int | None:
    """구간 소요시간 90백분위(초). 기록이 rules.travel_time_min_records 건 미만이면 None.

    - route: routeId
    - time_bin: 출발 시각의 KST 30분대 "HH:MM"(rules.time_bin_minutes)
    - history: 같은 노선·평일의 segment_time 기록. 최근 rules.travel_time_recent_weekdays
      평일만 쓴다. 시운전·공휴일 기록은 뺀다.
    - 백분위 계산 방법(보간 여부)은 합의 필요.

    None 이면 대안 비교의 destinationArrivalAt 이 null(도착시각 미제공)이다.
    """
    raise NotImplementedError
