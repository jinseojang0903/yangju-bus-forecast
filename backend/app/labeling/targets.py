"""목표 정류장(덕현초교 잠실행)의 노선별 정류장 순번과 그 확인.

순번의 기준은 constants.TARGET_STATION_SEQ_BY_ROUTE_NAME 이다. 리포트는 두 근거로 다시 확인한다.
- 위치 기록: stationId 가 목표 stationId 인 기록의 stationSeq.
- 기준정보 파일: <데이터폴더>/reference/<날짜>/targets.json 의 board.stationSeq
  (리포트 날짜 이하 중 가장 최근 폴더).
"""

import json
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.core.settings import COLLECT_TARGET, REFERENCE_DIRNAME, TARGETS_FILENAME, CollectTarget
from app.labeling.constants import TARGET_STATION_SEQ_BY_ROUTE_NAME
from app.labeling.records import PositionRecord, as_int


@dataclass(frozen=True)
class TargetStop:
    route_id: str
    route_name: str
    station_id: str
    station_seq: int


@dataclass(frozen=True)
class TargetSeqCheck:
    stop: TargetStop
    observed_seqs: Mapping[int, int] = field(default_factory=dict)  # 순번 → 관측 건수
    reference_seq: int | None = None  # targets.json 값. 파일·항목이 없으면 None

    @property
    def is_observed_mismatch(self) -> bool:
        return bool(self.observed_seqs) and self.stop.station_seq not in self.observed_seqs

    @property
    def is_reference_mismatch(self) -> bool:
        return self.reference_seq is not None and self.reference_seq != self.stop.station_seq

    @property
    def is_mismatch(self) -> bool:
        return self.is_observed_mismatch or self.is_reference_mismatch


def resolve_target_stops(
    target: CollectTarget = COLLECT_TARGET,
    seq_by_route_name: Mapping[str, int] = TARGET_STATION_SEQ_BY_ROUTE_NAME,
) -> dict[str, TargetStop]:
    """routeId → 목표 정류장. 설정에 routeId·stationId·순번이 없으면 ValueError."""
    if not target.board_station_id:
        raise ValueError("COLLECT_TARGET.board_station_id 가 없다")
    stops: dict[str, TargetStop] = {}
    for route in target.routes:
        if not route.route_id:
            raise ValueError(f"COLLECT_TARGET.routes[{route.route_name}].route_id 가 없다")
        seq = seq_by_route_name.get(route.route_name)
        if seq is None:
            raise ValueError(f"목표 정류장 순번이 없다: {route.route_name}")
        stops[route.route_id] = TargetStop(
            route_id=route.route_id,
            route_name=route.route_name,
            station_id=target.board_station_id,
            station_seq=seq,
        )
    return stops


def find_targets_file(data_dir: Path, day: date) -> Path | None:
    """day 이하 날짜 폴더 중 가장 최근의 targets.json. 없으면 None."""
    reference_root = data_dir / REFERENCE_DIRNAME
    if not reference_root.is_dir():
        return None
    candidates = [
        p for p in reference_root.glob(f"*/{TARGETS_FILENAME}") if p.parent.name <= day.isoformat()
    ]
    return max(candidates, key=lambda p: p.parent.name) if candidates else None


def load_reference_seqs(path: Path, station_id: str) -> dict[str, int]:
    """targets.json 에서 routeId → 승차 정류장 순번. board.stationId 가 다르면 넣지 않는다.

    파일을 읽지 못하거나 JSON 이 아니면 OSError·ValueError 를 그대로 올린다.
    """
    data = json.loads(path.read_text(encoding="utf-8"))
    routes = data.get("routes") if isinstance(data, dict) else None
    seqs: dict[str, int] = {}
    for route in routes if isinstance(routes, list) else []:
        if not isinstance(route, dict) or not isinstance(route.get("board"), dict):
            continue
        board = route["board"]
        seq = as_int(board.get("stationSeq"))
        route_id = route.get("route_id")
        is_same_station = str(board.get("stationId")) == station_id
        if isinstance(route_id, str) and seq is not None and is_same_station:
            seqs[route_id] = seq
    return seqs


def check_target_seqs(
    records: Iterable[PositionRecord],
    stops: Mapping[str, TargetStop],
    reference_seqs: Mapping[str, int],
) -> list[TargetSeqCheck]:
    observed: dict[str, Counter[int]] = {route_id: Counter() for route_id in stops}
    for record in records:
        stop = stops.get(record.route_id)
        if stop is not None and record.station_id == stop.station_id:
            observed[record.route_id][record.station_seq] += 1
    return [
        TargetSeqCheck(
            stop=stop,
            observed_seqs=dict(sorted(observed[route_id].items())),
            reference_seq=reference_seqs.get(route_id),
        )
        for route_id, stop in stops.items()
    ]
