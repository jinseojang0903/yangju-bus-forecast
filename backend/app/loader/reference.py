"""기준정보 폴더(<데이터폴더>/reference/<날짜>/)를 route·station·route_station 행으로 바꾼다.

- route: settings.YANGJU_ROUTES 의 모든 노선. 이름·수집 여부·라벨 대상·예약·심야·주기·메모는
  settings 가 기준이다. 유형·관할·지역·기점·종점은 노선 검색 기록(getBusRouteListv2)에서 채운다.
- station·route_station: 노선별 정류장 목록 기록(getBusRouteStationListv2). settings 에 없는 노선의
  기록은 route FK 때문에 넣지 않고 센다.
- 같은 노선의 기록이 여럿이면 파일 이름 순(수집기가 시각_순번 접두사를 붙인다)으로 나중 것을 쓴다.
"""

import json
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from app.collector.gbis import as_list
from app.core.settings import (
    GBIS_ROUTE_LIST,
    GBIS_ROUTE_STATIONS,
    REFERENCE_DIRNAME,
    REFERENCE_RECORD_SUFFIX,
    YANGJU_ROUTES,
    TargetRoute,
)
from app.labeling.records import as_id
from app.loader.rows import id_text, int4, text

_DATE_DIR_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


@dataclass(frozen=True)
class RouteRow:
    route_id: str
    route_name: str
    route_type_cd: str | None
    route_type_name: str | None
    admin_name: str | None
    region_name: str | None
    start_station_name: str | None
    end_station_name: str | None
    is_collected: bool
    is_label_target: bool
    is_reserved: bool
    is_night: bool
    collect_interval_sec: int | None
    note: str | None
    source_date: date | None  # 노선 검색 기록이 있을 때만. 없으면 DB 의 기존 값을 둔다


@dataclass(frozen=True)
class StationRow:
    station_id: str
    station_name: str
    mobile_no: str | None
    region_name: str | None
    x: float | None
    y: float | None
    source_date: date


@dataclass(frozen=True)
class RouteStationRow:
    route_id: str
    station_seq: int
    station_id: str
    is_turn_point: bool
    source_date: date


@dataclass
class ReferenceStats:
    record_files: int = 0
    route_list_records: int = 0
    route_station_records: int = 0
    unusable_records: int = 0  # JSON 이 아니거나 ok=false 이거나 다른 API
    unknown_route_records: int = 0  # settings 에 없는 노선의 정류장 목록
    skipped_station_items: int = 0  # stationId·stationSeq·stationName 이 없거나 순번이 겹치는 항목
    # 노선 routeId → 그 노선 목록에서 건너뛴 항목 수.
    # 0 보다 크면 그 노선의 순번 정리(삭제)를 하지 않는다.
    skipped_items_by_route: dict[str, int] = field(default_factory=dict)
    routes_with_details: list[str] = field(default_factory=list)


@dataclass
class ReferenceBatch:
    source_date: date
    folder: Path
    routes: list[RouteRow]
    stations: list[StationRow]
    route_stations: list[RouteStationRow]
    stats: ReferenceStats

    def route_station_groups(self) -> dict[str, list[RouteStationRow]]:
        """노선 routeId → 그 노선의 정류장 순서 행(목록 순서)."""
        groups: dict[str, list[RouteStationRow]] = {}
        for row in self.route_stations:
            groups.setdefault(row.route_id, []).append(row)
        return groups

    def can_trim(self, route_id: str) -> bool:
        """기준정보에 없어진 순번을 지워도 되는지. 그 노선 목록에 건너뛴 항목이 있으면 False
        (목록이 불완전할 수 있어 기존 순번을 지우지 않는다)."""
        return self.stats.skipped_items_by_route.get(route_id, 0) == 0


def find_reference_dir(data_dir: Path, day: date | None) -> Path | None:
    """day 가 있으면 그 날짜 폴더, 없으면 날짜 이름 폴더 중 가장 최근. 없으면 None."""
    root = data_dir / REFERENCE_DIRNAME
    if day is not None:
        folder = root / day.isoformat()
        return folder if folder.is_dir() else None
    if not root.is_dir():
        return None
    folders = [p for p in root.iterdir() if p.is_dir() and _is_date_name(p.name)]
    return max(folders, key=lambda p: p.name) if folders else None


def _is_date_name(name: str) -> bool:
    if not _DATE_DIR_PATTERN.fullmatch(name):
        return False
    try:
        date.fromisoformat(name)
    except ValueError:
        return False
    return True


# 좌표 허용 범위(경도 x, 위도 y). 벗어나거나 무한대·NaN 이면 NULL.
_LONGITUDE_RANGE = (-180.0, 180.0)
_LATITUDE_RANGE = (-90.0, 90.0)


def _coordinate(value: Any, bounds: tuple[float, float]) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value.strip() if isinstance(value, str) else value)
    except (ValueError, OverflowError):
        return None
    if not math.isfinite(number) or not bounds[0] <= number <= bounds[1]:
        return None
    return number


def _merge_station(old: StationRow | None, new: StationRow) -> StationRow:
    """같은 정류장이 여러 노선 목록에 나오면 값이 있는 쪽으로 합친다(나중 목록의 값 우선)."""
    if old is None:
        return new
    return StationRow(
        station_id=new.station_id,
        station_name=new.station_name,
        mobile_no=new.mobile_no if new.mobile_no is not None else old.mobile_no,
        region_name=new.region_name if new.region_name is not None else old.region_name,
        x=new.x if new.x is not None else old.x,
        y=new.y if new.y is not None else old.y,
        source_date=new.source_date,
    )


def _response_items(record: Mapping[str, Any], list_key: str) -> list[dict[str, Any]]:
    body = record.get("body")
    response = body.get("response") if isinstance(body, Mapping) else None
    msg_body = response.get("msgBody") if isinstance(response, Mapping) else None
    return as_list(msg_body.get(list_key)) if isinstance(msg_body, Mapping) else []


def _route_row(route: TargetRoute, details: Mapping[str, Any] | None, day: date) -> RouteRow:
    info = details or {}
    return RouteRow(
        route_id=route.route_id or "",
        route_name=route.route_name,
        route_type_cd=id_text(info.get("routeTypeCd")),
        route_type_name=text(info.get("routeTypeName")),
        admin_name=text(info.get("adminName")),
        region_name=text(info.get("regionName")),
        start_station_name=text(info.get("startStationName")),
        end_station_name=text(info.get("endStationName")),
        is_collected=route.collect,
        is_label_target=route.label_target,
        is_reserved=route.is_reserved,
        is_night=route.is_night,
        collect_interval_sec=route.interval_sec if route.collect else None,
        note=route.note or None,
        source_date=day if details is not None else None,
    )


def read_reference(
    folder: Path, source_date: date, routes: Sequence[TargetRoute] = YANGJU_ROUTES
) -> ReferenceBatch:
    """폴더의 *.record.json 을 읽어 행을 만든다. 파일 읽기 OSError 는 그대로 올린다."""
    stats = ReferenceStats()
    known_ids = {r.route_id for r in routes if r.route_id}
    route_details: dict[str, dict[str, Any]] = {}
    station_lists: dict[str, list[dict[str, Any]]] = {}

    for path in sorted(folder.glob(f"*{REFERENCE_RECORD_SUFFIX}")):
        stats.record_files += 1
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (ValueError, RecursionError):  # 깨진 JSON·깊은 중첩
            stats.unusable_records += 1
            continue
        if not isinstance(record, dict) or record.get("ok") is not True:
            stats.unusable_records += 1
            continue
        api = record.get("api")
        if api == GBIS_ROUTE_LIST.api:
            stats.route_list_records += 1
            for item in _response_items(record, GBIS_ROUTE_LIST.list_key):
                route_id = as_id(item.get("routeId"))
                if route_id in known_ids:
                    route_details[route_id] = item
        elif api == GBIS_ROUTE_STATIONS.api:
            stats.route_station_records += 1
            params = record.get("params")
            route_id = as_id(params.get("routeId")) if isinstance(params, Mapping) else None
            if route_id not in known_ids or route_id is None:
                stats.unknown_route_records += 1
                continue
            station_lists[route_id] = _response_items(record, GBIS_ROUTE_STATIONS.list_key)
        else:
            stats.unusable_records += 1

    route_rows = [
        _route_row(r, route_details.get(r.route_id), source_date) for r in routes if r.route_id
    ]
    stats.routes_with_details = [r.route_name for r in routes if r.route_id in route_details]

    stations: dict[str, StationRow] = {}
    route_stations: list[RouteStationRow] = []
    for route_id, items in station_lists.items():
        seen_seqs: set[int] = set()
        for item in items:
            station_id = id_text(item.get("stationId"))
            station_seq = int4(item.get("stationSeq"))
            station_name = text(item.get("stationName"))
            if (
                station_id is None
                or station_seq is None
                or station_seq < 1
                or station_name is None
                or station_seq in seen_seqs
            ):
                stats.skipped_station_items += 1
                stats.skipped_items_by_route[route_id] = (
                    stats.skipped_items_by_route.get(route_id, 0) + 1
                )
                continue
            seen_seqs.add(station_seq)
            station = StationRow(
                station_id=station_id,
                station_name=station_name,
                mobile_no=text(item.get("mobileNo")),  # 원본은 앞에 공백이 붙는다(" 39624")
                region_name=text(item.get("regionName")),
                x=_coordinate(item.get("x"), _LONGITUDE_RANGE),
                y=_coordinate(item.get("y"), _LATITUDE_RANGE),
                source_date=source_date,
            )
            stations[station_id] = _merge_station(stations.get(station_id), station)
            route_stations.append(
                RouteStationRow(
                    route_id=route_id,
                    station_seq=station_seq,
                    station_id=station_id,
                    is_turn_point=text(item.get("turnYn")) == "Y",
                    source_date=source_date,
                )
            )

    return ReferenceBatch(
        source_date=source_date,
        folder=folder,
        routes=route_rows,
        stations=list(stations.values()),
        route_stations=route_stations,
        stats=stats,
    )
