"""백테스트 테스트 공용 도구: 합성 운행편과, 손으로 계산한 기대값이 있는 장면.

합성 운행편(trip_records)은 목표 정류장 도착 20분 전부터 1분마다 기록이 있고 3분마다 한 정류장씩
나아간다. 도착 15·10·5분 전의 잔여석은 모두 seats 이고, 도착 1분 전(직전 정류장 출발 기록)의
잔여석이 last_seat 이다. 그래서 정답은 strict 이고 last_seat 이 0 이면 1 이다.
앞 운행편과 도착 시각이 h 분 차이 나면 모든 선행시간의 앞차 간격이 h 분이다.

장면(build_scenario_records) — 모든 운행편의 앞차 간격은 15분이다.
- 기본 날 10일(G1300): P 05:50(예보 시간대 밖. 앞차가 없어 사례도 아니다), T1 06:05·T2 06:20
  (잔여석 5 → 0석 도착), T3 06:35·T4 06:50·T5 07:05(잔여석 20 → 0석 아님).
- 공휴일 2026-10-09(settings.SERVICE_HOURS.holidays): 기본 날과 같은 운행편. 모두 빠져야 한다.
- 시험 날 2026-10-22(G1300): P 05:50, T1 06:05(5 → 0석), T2 06:20(5 → 0석 아님),
  T3 06:35(20 → 0석), T4 06:50(20 → 아님), T5 07:05(0 → 0석), T6 07:20(0 → 아님),
  T7 07:35(잔여석 −1 → 아님), X 07:50(시운전, 20 → 아님), T8 08:05(20 → 아님).
- 시험 날 1306: 06:30 한 대(잔여석 0 → 0석). 앞차가 없다.

기대값(G1300, 손 계산. 사례 20건 이상이어야 방법 B 가 확률을 낸다):
- 잔여석 5 묶음: 시험 날 전까지 사례 20건(모두 0석). 기본 날에는 20건이 안 돼 모두 보류.
  시험 날 T1 은 20건 중 20건 → 경보 → 맞힌 만차. T2 는 경보 → 잘못 알린 만차.
- 잔여석 20 묶음: 날마다 3건씩 쌓인다(모두 0석 아님). 같은 날 앞선 운행은 도착(확정)이 기준 시각
  보다 일러야 사례다. 15분 간격이라 선행시간 15분에서는 바로 앞 운행이 '같은 시각'이라 빠진다.
  기본 날에 확률을 낸 운행편: 선행시간 5·10분 10건, 15분 9건(모두 맞힌 비만차).
  시험 날 T3 은 30건 중 0건 → 놓친 만차. T4·T8 은 맞힌 비만차(T3 이 사례에 들면 30건 중 1건).
- 잔여석 0 묶음(T5·T6)은 사례가 없거나 1건이라 보류. T7 은 잔여석을 몰라 입력 누락.
- 방법 A: 잔여석 0 인 T5(맞힌 만차)·T6(잘못 알린 만차)만 경보. T7 은 판단 불가.
"""

from collections import defaultdict
from datetime import date, datetime, time, timedelta
from pathlib import Path

from app.core.settings import KST, MODE_RUN, MODE_TRIAL, SERVICE_HOURS
from app.labeling.constants import STATE_ARRIVED, STATE_DEPARTED, STATE_PASSING
from app.labeling.records import PositionRecord
from app.labeling.targets import TargetStop
from tests.labeling.helpers import (
    BOARD_STATION_ID,
    G1300,
    R1306,
    gbis_item,
    location_line,
    pos,
    write_jsonl,
)

TARGET_STOPS = {
    G1300: TargetStop(G1300, "G1300", BOARD_STATION_ID, 13),
    R1306: TargetStop(R1306, "1306", BOARD_STATION_ID, 11),
}
ROUTE_NAMES = {G1300: "G1300", R1306: "1306"}

BASE_DAYS = (
    date(2026, 10, 7),
    date(2026, 10, 8),
    date(2026, 10, 12),
    date(2026, 10, 13),
    date(2026, 10, 14),
    date(2026, 10, 15),
    date(2026, 10, 16),
    date(2026, 10, 19),
    date(2026, 10, 20),
    date(2026, 10, 21),
)
HOLIDAY = date(2026, 10, 9)
TEST_DAY = date(2026, 10, 22)

_MINUTES_BEFORE_ARRIVAL = 20
_MINUTES_PER_STOP = 3


def moment(day: date, at: time) -> datetime:
    return datetime.combine(day, at, tzinfo=KST)


def trip_records(
    day: date,
    veh_id: str,
    arrival: time,
    *,
    seats: int,
    last_seat: int,
    route_id: str = G1300,
    is_departure_recorded: bool = True,
    mode: str = MODE_RUN,
    is_holiday: bool = False,
) -> list[PositionRecord]:
    """목표 정류장에 arrival 에 도착하는 운행편 하나의 위치 기록(모듈 설명 참조).

    is_departure_recorded 가 False 면 직전 정류장 출발(stateCd 2) 기록이 없어
    정답 등급이 relaxed 다.
    """
    target_seq = TARGET_STOPS[route_id].station_seq
    arrival_at = moment(day, arrival)

    def record(minutes_before: int, seq: int, state: int, seat: int) -> PositionRecord:
        return pos(
            arrival_at - timedelta(minutes=minutes_before),
            seq,
            state,
            seat,
            route_id=route_id,
            veh_id=veh_id,
            station_id=BOARD_STATION_ID if seq == target_seq else None,
            mode=mode,
            is_holiday=is_holiday,
        )

    records = [
        record(m, target_seq - 1 - (m - 1) // _MINUTES_PER_STOP, STATE_PASSING, seats)
        for m in range(_MINUTES_BEFORE_ARRIVAL, 1, -1)
    ]
    last_state = STATE_DEPARTED if is_departure_recorded else STATE_PASSING
    records.append(record(1, target_seq - 1, last_state, last_seat))
    records.append(record(0, target_seq, STATE_ARRIVED, last_seat))
    records.append(record(-1, target_seq + 1, STATE_PASSING, last_seat))
    return records


def _veh_id(day: date, index: int) -> str:
    # 운행편마다 다른 차량으로 둔다(같은 차량이면 15분 간격 운행이 한 운행편으로 이어진다).
    return f"2350{day.day:02d}{index:03d}"


def _base_day_records(day: date) -> list[PositionRecord]:
    plan = [
        (time(5, 50), 5, 0),  # P
        (time(6, 5), 5, 0),  # T1
        (time(6, 20), 5, 0),  # T2
        (time(6, 35), 20, 20),  # T3
        (time(6, 50), 20, 20),  # T4
        (time(7, 5), 20, 20),  # T5
    ]
    return [
        r
        for index, (arrival, seats, last_seat) in enumerate(plan)
        for r in trip_records(day, _veh_id(day, index), arrival, seats=seats, last_seat=last_seat)
    ]


def _test_day_records(day: date) -> list[PositionRecord]:
    plan = [
        (time(5, 50), 5, 0, MODE_RUN),  # P
        (time(6, 5), 5, 0, MODE_RUN),  # T1
        (time(6, 20), 5, 3, MODE_RUN),  # T2
        (time(6, 35), 20, 0, MODE_RUN),  # T3
        (time(6, 50), 20, 20, MODE_RUN),  # T4
        (time(7, 5), 0, 0, MODE_RUN),  # T5
        (time(7, 20), 0, 2, MODE_RUN),  # T6
        (time(7, 35), -1, 4, MODE_RUN),  # T7
        (time(7, 50), 20, 20, MODE_TRIAL),  # X
        (time(8, 5), 20, 20, MODE_RUN),  # T8
    ]
    records = [
        r
        for index, (arrival, seats, last_seat, mode) in enumerate(plan)
        for r in trip_records(
            day, _veh_id(day, index), arrival, seats=seats, last_seat=last_seat, mode=mode
        )
    ]
    records += trip_records(
        day, _veh_id(day, 900), time(6, 30), seats=0, last_seat=0, route_id=R1306
    )
    return records


def build_scenario_records() -> dict[date, list[PositionRecord]]:
    """날짜 → 그날의 위치 기록(모듈 설명의 장면)."""
    assert HOLIDAY in SERVICE_HOURS.holidays
    by_day = {day: _base_day_records(day) for day in (*BASE_DAYS, HOLIDAY)}
    by_day[TEST_DAY] = _test_day_records(TEST_DAY)
    return dict(sorted(by_day.items()))


def write_day_file(data_dir: Path, day: date, records: list[PositionRecord]) -> Path:
    """위치 기록을 수집기 JSONL(<data_dir>/<날짜>/raw_poll.jsonl) 형식으로 쓴다."""
    polls: dict[tuple[datetime, str, str | None], list[PositionRecord]] = defaultdict(list)
    for r in records:
        polls[(r.collected_at, r.route_id, r.mode)].append(r)
    lines = [
        location_line(
            collected_at,
            [
                gbis_item(
                    r.veh_id,
                    r.station_seq,
                    r.state_cd if r.state_cd is not None else STATE_PASSING,
                    r.remain_seat_cnt if r.remain_seat_cnt is not None else -1,
                    route_id=route_id,
                    station_id=r.station_id,
                )
                for r in poll
            ],
            route_id=route_id,
            mode=mode or MODE_RUN,
            is_holiday=any(r.is_holiday for r in poll),
        )
        for (collected_at, route_id, mode), poll in sorted(polls.items(), key=lambda kv: kv[0][0])
    ]
    return write_jsonl(data_dir / day.isoformat() / "raw_poll.jsonl", lines)
