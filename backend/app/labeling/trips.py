"""운행편 재구성.

키는 (날짜, routeId, vehId) 다. 같은 차량이 하루에 여러 번 운행하므로 수집 시각 순으로 정렬한 뒤
다음 중 하나면 새 운행편으로 나눈다.
- 앞 기록과의 공백이 TRIP_SPLIT_GAP_SEC 보다 길다.
- 정류장 순번이 앞 기록보다 TRIP_SPLIT_SEQ_DROP 이상 줄었다(종점 회차 뒤 기점에서 다시 시작).
수집 간격(interval_sec)과 무관하게 같은 기준을 쓴다. 10·30·40·60초 기록이 섞여도 된다.
"""

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

from app.core.settings import KST
from app.labeling.constants import TRIP_SPLIT_GAP_SEC, TRIP_SPLIT_SEQ_DROP
from app.labeling.records import PositionRecord


@dataclass(frozen=True)
class Trip:
    service_date: date  # 수집 시각(KST)의 날짜
    route_id: str
    veh_id: str
    trip_index: int  # 같은 날·노선·차량 안에서 1부터
    records: tuple[PositionRecord, ...]  # 수집 시각 순, 1건 이상

    @property
    def trip_key(self) -> str:
        return f"{self.service_date.isoformat()}_{self.route_id}_{self.veh_id}_{self.trip_index}"

    @property
    def start_at(self) -> datetime:
        return self.records[0].collected_at

    @property
    def end_at(self) -> datetime:
        return self.records[-1].collected_at

    @property
    def plate_no(self) -> str | None:
        return next((r.plate_no for r in reversed(self.records) if r.plate_no), None)

    @property
    def is_trial(self) -> bool:
        return any(r.is_trial for r in self.records)

    @property
    def is_holiday(self) -> bool:
        return any(r.is_holiday for r in self.records)

    @property
    def interval_secs(self) -> tuple[int, ...]:
        """기록에 남은 수집 간격(초)들. 오름차순, 중복 없음. 간격이 없는 기록은 뺀다."""
        return tuple(sorted({r.interval_sec for r in self.records if r.interval_sec is not None}))


def _is_new_trip(
    previous: PositionRecord, current: PositionRecord, *, gap_sec: float, seq_drop: int
) -> bool:
    gap = (current.collected_at - previous.collected_at).total_seconds()
    return gap > gap_sec or previous.station_seq - current.station_seq >= seq_drop


def _split(
    records: list[PositionRecord], *, gap_sec: float, seq_drop: int
) -> list[list[PositionRecord]]:
    segments: list[list[PositionRecord]] = []
    for record in records:
        if segments and not _is_new_trip(
            segments[-1][-1], record, gap_sec=gap_sec, seq_drop=seq_drop
        ):
            segments[-1].append(record)
        else:
            segments.append([record])
    return segments


def build_trips(
    records: Iterable[PositionRecord],
    *,
    gap_sec: float = TRIP_SPLIT_GAP_SEC,
    seq_drop: int = TRIP_SPLIT_SEQ_DROP,
) -> list[Trip]:
    """위치 기록을 운행편으로 묶는다.

    입력 순서는 상관없다. 결과는 (날짜, routeId, 시작 시각, vehId) 순이다.
    """
    groups: dict[tuple[date, str, str], list[PositionRecord]] = defaultdict(list)
    for record in records:
        day = record.collected_at.astimezone(KST).date()
        groups[(day, record.route_id, record.veh_id)].append(record)

    trips: list[Trip] = []
    for (day, route_id, veh_id), group in groups.items():
        group.sort(key=lambda r: r.collected_at)
        for index, segment in enumerate(_split(group, gap_sec=gap_sec, seq_drop=seq_drop), start=1):
            trips.append(Trip(day, route_id, veh_id, index, tuple(segment)))
    trips.sort(key=lambda t: (t.service_date, t.route_id, t.start_at, t.veh_id))
    return trips
