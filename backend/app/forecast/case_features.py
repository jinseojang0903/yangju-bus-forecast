"""사례 특징 계산: 운행편이 목표 정류장 도착 L분 전일 때의 잔여석과 앞차 간격.

아래 정의는 회의 안건으로 미정이던 것을 수집 데이터를 보기 전에 고정한 값이다(결정 2026-11-13).
결과가 나쁘게 나와도 바꾸지 않는다. 바꿔야 하면 settings.CASE_FEATURE_RULE_VERSION 을 올린다.

- 도착 시각: 운행편에서 정류장 순번이 목표 순번 이상인 첫 기록의 수집 시각
  (정답 라벨과 같은 정의, labeling.trips.first_reach_index).
- 기준 시각(reference_at): 도착 시각 − L분.
- '그 시점' 기록: 그 운행편에서 수집 시각이 기준 시각 이하인 마지막 기록. 기준 시각보다
  settings.CASE_FEATURE_REFERENCE_MAX_AGE_SEC 넘게 오래됐으면 쓰지 않는다.
- 잔여석: 그 기록의 잔여석. 없거나 음수(−1 = 정보 없음)이면 seat_unknown.
  0 은 '0석'이고 −1 은 '모름'이다. 둘을 섞지 않는다.
- 앞차 간격(분): 그 기록의 정류장 순번을 s 라 할 때
  (내 버스가 s 에 처음 닿은 시각) − (같은 날·같은 노선의 바로 앞 운행편이 s 에 처음 닿은 시각).
  '처음 닿은 시각'은 순번이 s 이상인 첫 기록의 시각이고, 그 앞에 순번이 s 미만인 기록이 있어야
  관측된 것으로 본다(첫 기록부터 s 이상이면 언제 지났는지 모른다).
  앞 운행편은 s 통과 시각이 내 통과 시각보다 이른 것 중 가장 늦은 운행편이다.
  같은 수집 시각에 s 를 처음 지난 것으로 관측된 운행편은 앞차로 보지 않는다
  (한 번의 호출 안에서는 어느 차가 먼저 지났는지 알 수 없다).
- 미래 정보 누출 금지: 잔여석과 간격은 기준 시각 이하의 기록만으로 정한다.
  (기준 시각 자체는 actual_arrival 기준이라 실제 도착을 알아야 정해진다. 지난 운행 전용이다.)
"""

from bisect import bisect_right
from collections.abc import Sequence
from datetime import datetime, timedelta

from app.core.settings import CASE_FEATURE_REFERENCE_MAX_AGE_SEC, CASE_FEATURE_RULE_VERSION
from app.forecast.interfaces import CaseFeature, ReferenceBasis, UncomputableReason
from app.labeling.records import PositionRecord
from app.labeling.targets import TargetStop
from app.labeling.trips import Trip, first_reach_index

_SECONDS_PER_MINUTE = 60


def _observed_pass_at(
    records: Sequence[PositionRecord], station_seq: int, *, not_after: datetime
) -> datetime | None:
    """운행편이 station_seq 에 처음 닿은 시각. 관측되지 않았거나 not_after 보다 늦으면 None.

    첫 기록부터 이미 station_seq 이상이면 통과 시각을 모르므로 관측되지 않은 것으로 본다.
    """
    index = first_reach_index(records, station_seq)
    if index is None or index == 0:
        return None
    pass_at = records[index].collected_at
    return pass_at if pass_at <= not_after else None


def _preceding_pass_at(
    trip: Trip,
    day_trips: Sequence[Trip],
    station_seq: int,
    *,
    my_pass_at: datetime,
    reference_at: datetime,
) -> datetime | None:
    """같은 날·같은 노선의 바로 앞 운행편이 station_seq 를 지난 시각. 앞 운행편이 없으면 None."""
    pass_times = (
        _observed_pass_at(other.records, station_seq, not_after=reference_at)
        for other in day_trips
        if other.service_date == trip.service_date
        and other.route_id == trip.route_id
        and other.trip_key != trip.trip_key
    )
    earlier = [moment for moment in pass_times if moment is not None and moment < my_pass_at]
    return max(earlier, default=None)


def compute_case_features(
    trip: Trip,
    target_station: TargetStop,
    lead_time_min: int,
    reference_basis: ReferenceBasis,
    rule_version: str = CASE_FEATURE_RULE_VERSION,
    *,
    day_trips: Sequence[Trip] = (),
) -> CaseFeature:
    """운행편 trip 이 target_station 에 도착하기 lead_time_min 분 전 시점의 특징.

    입력
    - trip: app.labeling.trips.Trip(같은 날·노선·차량의 위치 기록 묶음)
    - target_station: 목표 정류장과 그 노선 순번(app.labeling.targets.TargetStop)
    - lead_time_min: settings.FORECAST_RULES.lead_times_min 중 하나(양수)
    - reference_basis: 시점 기준. 지금은 "actual_arrival"(지난 운행)만 구현했다.
    - rule_version: 특징 계산 규칙 버전. API rulesVersion 과 따로 둔다
    - day_trips: 앞차 간격 계산에 쓰는 운행편 목록. 같은 날·같은 노선의 다른 운행편만 골라
      쓰므로 그날 전체(또는 여러 날)를 그대로 넘겨도 된다. trip 자신이 들어 있어도 된다.
      시운전·공휴일 운행편도 실제로 앞서 달린 차량이므로 빼지 않고 넘긴다
      (사례·평가 대상에서 빼는 것은 호출하는 쪽이 따로 한다).

    출력: CaseFeature. 기준 시각·잔여석·앞차 간격 중 하나라도 정할 수 없으면 computable=False
    와 uncomputable_reason 이다. 아래 순서로 확인하고 처음 걸린 사유 하나만 남긴다.
    걸리기 전에 정한 값(reference_at, seats)은 그대로 담는다.
    1. no_target_arrival: 목표 순번 이상인 기록이 없다(reference_at = None)
    2. no_record_near_reference: 기준 시각 이하 기록이 없거나 너무 오래됐다
    3. seat_unknown: 그 기록의 잔여석이 없거나 음수다(0 으로 바꾸지 않는다)
    4. no_record_near_reference: 내 버스가 그 순번에 닿은 시각이 관측되지 않았다
    5. no_preceding_vehicle: 그 순번을 기준 시각 이전에 먼저 지난 운행편이 day_trips 에 없다

    실패 조건
    - reference_basis 가 "predicted_arrival" 이면 NotImplementedError. 지금 운행의 기준 시각은
      도착 API 기록(도착 예상 predictTimeSec)이 있어야 정하는데, 도착 기록을 읽는 부분이
      이번 범위 밖이다.
    - lead_time_min 이 0 이하이거나 trip 과 target_station 의 노선이 다르면 ValueError.
    """
    if reference_basis != "actual_arrival":
        raise NotImplementedError(
            "predicted_arrival 은 도착 API 기록이 있어야 계산한다(아직 구현하지 않았다)"
        )
    if lead_time_min <= 0:
        raise ValueError(f"lead_time_min 은 양수여야 한다: {lead_time_min}")
    if trip.route_id != target_station.route_id:
        raise ValueError(
            f"운행편과 목표 정류장의 노선이 다르다: {trip.route_id} != {target_station.route_id}"
        )

    def feature(
        reason: UncomputableReason | None,
        *,
        reference_at: datetime | None = None,
        seats: int | None = None,
        headway_min: float | None = None,
    ) -> CaseFeature:
        return CaseFeature(
            trip_key=trip.trip_key,
            service_date=trip.service_date,
            route_id=trip.route_id,
            station_id=target_station.station_id,
            lead_time_min=lead_time_min,
            reference_at=reference_at,
            reference_basis=reference_basis,
            seats=seats,
            headway_min=headway_min,
            computable=reason is None,
            uncomputable_reason=reason,
            rule_version=rule_version,
        )

    records = trip.records
    arrival_index = first_reach_index(records, target_station.station_seq)
    if arrival_index is None:
        return feature("no_target_arrival")
    reference_at = records[arrival_index].collected_at - timedelta(minutes=lead_time_min)

    reference_index = bisect_right(records, reference_at, key=lambda r: r.collected_at) - 1
    if reference_index < 0:
        return feature("no_record_near_reference", reference_at=reference_at)
    reference_record = records[reference_index]
    age_sec = (reference_at - reference_record.collected_at).total_seconds()
    if age_sec > CASE_FEATURE_REFERENCE_MAX_AGE_SEC:
        return feature("no_record_near_reference", reference_at=reference_at)

    seats = reference_record.remain_seat_cnt
    if seats is None or seats < 0:
        return feature("seat_unknown", reference_at=reference_at)

    station_seq = reference_record.station_seq
    my_pass_at = _observed_pass_at(records, station_seq, not_after=reference_at)
    if my_pass_at is None:
        return feature("no_record_near_reference", reference_at=reference_at, seats=seats)
    preceding_pass_at = _preceding_pass_at(
        trip, day_trips, station_seq, my_pass_at=my_pass_at, reference_at=reference_at
    )
    if preceding_pass_at is None:
        return feature("no_preceding_vehicle", reference_at=reference_at, seats=seats)

    headway_min = (my_pass_at - preceding_pass_at).total_seconds() / _SECONDS_PER_MINUTE
    return feature(None, reference_at=reference_at, seats=seats, headway_min=headway_min)
