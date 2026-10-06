"""정답 라벨 '도착 상태 0석'과 등급(strict·relaxed·unconfirmed).

고정 규칙(CLAUDE.md '바꾸지 않는 규칙 > 정답 라벨'):
    직전 정류장의 '출발' 기록(stateCd 2) 이후, 내 정류장 도착 전 마지막 잔여석이 0이면 1, 아니면 0.
    도착 뒤 값은 쓰지 않는다. 출발 기록 누락, 잔여석 −1, 1분 사이 통과는 '정답 미확인'.

해석(애매한 부분은 모듈 보고에 적었다):
- 도착 = 목표 순번 이상인 첫 기록. 그 기록과 이후 기록은 보지 않는다.
- strict: 도착 전 기록 중 직전 정류장(목표 − 1)의 stateCd 2 기록이 있다. 라벨 근거는
  그 출발 기록부터(출발 기록 포함) 도착 전까지의 마지막 기록, 곧 도착 직전 기록이다.
  1분 수집에서는 출발 기록 다음 기록이 대개 도착 기록이므로,
  출발 기록을 빼면 거의 모두 미확인이 된다.
- relaxed: 출발 기록은 없지만 직전 정류장 순번이고 stateCd 가 1(도착)이 아닌 기록이 있다.
  stateCd 가 없는 기록은 이동 중으로 보지 않는다. 라벨 근거는 그런 기록 중 마지막 기록이다.
- 근거 기록의 잔여석이 −1(또는 음수)이거나 없으면 seat_unknown 으로 미확인.
  앞 기록으로 물러서지 않는다.
- 예약버스: 위치 v2 응답(실제 픽스처)에 예약버스를 가리는 필드가 없어 사유 reservation_bus 를
  만들지 않는다. 판정 필드를 찾으면 UnconfirmedReason 에 추가하고 label_trip 첫머리에서 거른다.
- 시운전(trial)·공휴일은 등급을 그대로 계산하고 is_trial·is_holiday 플래그로만 표시한다.
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from app.labeling.constants import STATE_ARRIVED, STATE_DEPARTED
from app.labeling.records import PositionRecord
from app.labeling.trips import Trip


class Grade(StrEnum):
    STRICT = "strict"
    RELAXED = "relaxed"
    UNCONFIRMED = "unconfirmed"


class UnconfirmedReason(StrEnum):
    # 직전 정류장 기록은 있지만 출발(2)도 이동 중(≠1)도 아니다(도착 기록만 있음).
    NO_DEPARTURE_OR_MOVING_RECORD = "no_departure_or_moving_record"
    # 근거 기록의 잔여석이 −1 이거나 없다.
    SEAT_UNKNOWN = "seat_unknown"
    # 직전 정류장 순번 기록 없이 그 앞에서 바로 목표 순번 이상으로 건너뛰었다.
    PASSED_WITHIN_POLL = "passed_within_poll"
    # 운행편 안에서 목표 순번에 닿지 않았다(수집 창이 끝났거나 기록이 끊김).
    NEVER_REACHED_TARGET = "never_reached_target"
    # 운행편의 첫 기록이 이미 목표 순번 이상이다(수집 시작 전에 지나갔거나 공백 뒤 이어진 조각).
    # 메인 지시 목록에 없는 사유다. never_reached_target 과 섞이지 않게 따로 둔다.
    NO_RECORD_BEFORE_TARGET = "no_record_before_target"


# 목표에 도착한 운행편이 아니라서 '도착 운행편 대비 제외 비율'의 분모에서 빼는 사유.
NOT_REACHED_REASONS: frozenset[UnconfirmedReason] = frozenset(
    {UnconfirmedReason.NEVER_REACHED_TARGET, UnconfirmedReason.NO_RECORD_BEFORE_TARGET}
)


@dataclass(frozen=True)
class TripLabel:
    service_date: date
    route_id: str
    veh_id: str
    trip_index: int
    plate_no: str | None
    trip_start_at: datetime
    target_seq: int
    target_arrival_at: datetime | None  # 목표 순번 이상인 첫 기록의 수집 시각
    grade: Grade
    label: int | None  # 1 = 도착 상태 0석, 0 = 아님, None = 미확인
    last_seat: int | None  # 라벨 근거 잔여석(미확인이면 참고값 또는 None)
    last_seat_at: datetime | None  # 근거 기록의 수집 시각
    reason: UnconfirmedReason | None  # 미확인일 때만
    is_trial: bool
    is_holiday: bool
    interval_secs: tuple[int, ...]
    record_count: int

    @property
    def trip_key(self) -> str:
        return f"{self.service_date.isoformat()}_{self.route_id}_{self.veh_id}_{self.trip_index}"

    @property
    def is_confirmed(self) -> bool:
        return self.grade is not Grade.UNCONFIRMED

    @property
    def interval_sec(self) -> int | tuple[int, ...] | None:
        """수집 간격. 하나면 그 값, 섞였으면 목록, 기록이 없으면 None."""
        if not self.interval_secs:
            return None
        if len(self.interval_secs) == 1:
            return self.interval_secs[0]
        return self.interval_secs


def label_trip(trip: Trip, target_seq: int) -> TripLabel:
    """운행편 하나에 목표 정류장(target_seq) 기준 등급과 라벨을 붙인다.

    target_seq 가 2 미만이면 직전 정류장이 없으므로 ValueError.
    """
    if target_seq < 2:
        raise ValueError(f"target_seq 는 2 이상이어야 한다: {target_seq}")
    prev_seq = target_seq - 1
    records = trip.records

    arrival_index = next((i for i, r in enumerate(records) if r.station_seq >= target_seq), None)
    arrival_at = records[arrival_index].collected_at if arrival_index is not None else None

    def result(
        grade: Grade,
        *,
        reason: UnconfirmedReason | None = None,
        basis: PositionRecord | None = None,
    ) -> TripLabel:
        seat = basis.remain_seat_cnt if basis is not None else None
        label: int | None = None
        if grade is not Grade.UNCONFIRMED and seat is not None:
            label = 1 if seat == 0 else 0
        return TripLabel(
            service_date=trip.service_date,
            route_id=trip.route_id,
            veh_id=trip.veh_id,
            trip_index=trip.trip_index,
            plate_no=trip.plate_no,
            trip_start_at=trip.start_at,
            target_seq=target_seq,
            target_arrival_at=arrival_at,
            grade=grade,
            label=label,
            last_seat=seat,
            last_seat_at=basis.collected_at if basis is not None else None,
            reason=reason,
            is_trial=trip.is_trial,
            is_holiday=trip.is_holiday,
            interval_secs=trip.interval_secs,
            record_count=len(records),
        )

    if arrival_index is None:
        return result(Grade.UNCONFIRMED, reason=UnconfirmedReason.NEVER_REACHED_TARGET)
    before = records[:arrival_index]  # 도착 전 기록만. 도착 기록과 그 뒤는 쓰지 않는다.
    if not before:
        return result(Grade.UNCONFIRMED, reason=UnconfirmedReason.NO_RECORD_BEFORE_TARGET)

    at_prev = [r for r in before if r.station_seq == prev_seq]
    if not at_prev:
        return result(Grade.UNCONFIRMED, reason=UnconfirmedReason.PASSED_WITHIN_POLL)

    if any(r.state_cd == STATE_DEPARTED for r in at_prev):
        # 출발 기록(포함) 이후 ~ 도착 전의 마지막 기록 = 도착 직전 기록.
        grade, basis = Grade.STRICT, before[-1]
    else:
        moving = [r for r in at_prev if r.state_cd is not None and r.state_cd != STATE_ARRIVED]
        if not moving:
            return result(Grade.UNCONFIRMED, reason=UnconfirmedReason.NO_DEPARTURE_OR_MOVING_RECORD)
        grade, basis = Grade.RELAXED, moving[-1]

    if basis.remain_seat_cnt is None or basis.remain_seat_cnt < 0:
        return result(Grade.UNCONFIRMED, reason=UnconfirmedReason.SEAT_UNKNOWN, basis=basis)
    return result(grade, basis=basis)
