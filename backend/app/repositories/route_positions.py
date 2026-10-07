"""노선 지도용 파일 읽기(계약 4.8절). 수집기가 쓴 파일만 읽고 GBIS 를 부르지 않는다.

- 정류장 좌표: discover 가 저장한 노선 API 원본 기록 중 가장 최근 날짜의 것.
  <데이터폴더>/reference/<YYYY-MM-DD>/*_getBusRouteStationListv2_routeId-<노선>.record.json
- 차량 위치: <데이터폴더>/<YYYY-MM-DD>/raw_poll.jsonl 에서 그 노선의 쓸 수 있는 마지막 위치 줄.
  하루 파일이 수십 MB 가 되므로 끝에서부터 거꾸로, 파일당 상한까지만 읽는다.
  오늘 줄이 없으면(파일 없음·상한 안에 없음·읽기 실패) 전날 폴더로 거슬러 간다.

route_id 는 경계(라우터·서비스)에서 숫자 형식과 지원 노선 목록을 확인한 값만 받는다.
파일 경로·glob 패턴에 들어가기 때문에 여기서도 형식을 한 번 더 확인한다.
"""

import json
import logging
import math
import os
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.collector.gbis import as_list, normalize_result_code
from app.collector.storage import raw_poll_path
from app.core.settings import (
    GBIS_BUS_LOCATION,
    GBIS_NO_RESULT_CODES,
    GBIS_ROUTE_STATIONS,
    GBIS_SUCCESS_CODES,
    MODE_TRIAL,
    RAW_POLL_TAIL_CHUNK_BYTES,
    RAW_POLL_TAIL_MAX_BYTES_PER_FILE,
    REFERENCE_DIRNAME,
    REFERENCE_RECORD_SUFFIX,
)
from app.labeling.records import (
    PositionRecord,
    as_id,
    as_int,
    extract_positions,
    parse_collected_at,
)
from app.schemas.common import ROUTE_ID_PATTERN

logger = logging.getLogger(__name__)

_DATE_DIR_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_ROUTE_ID_RE = re.compile(ROUTE_ID_PATTERN)


@dataclass(frozen=True)
class RouteStation:
    station_seq: int
    station_id: str
    name: str
    lat: float
    lng: float


@dataclass(frozen=True)
class RouteStations:
    """노선 정류장 목록(stationSeq 오름차순)과 회차 순번."""

    turn_seq: int
    stations: tuple[RouteStation, ...]


@dataclass(frozen=True)
class LatestPositions:
    """노선의 마지막 위치 기록 1줄. 차량이 없으면 records 가 빈 튜플이다."""

    collected_at: datetime  # KST
    records: tuple[PositionRecord, ...]


def _require_route_id(route_id: str) -> None:
    # 경계에서 검증하지만 경로·glob 에 들어가는 값이라 여기서도 한 번 더 막는다.
    if not _ROUTE_ID_RE.fullmatch(route_id):
        raise ValueError("route_id 형식이 아니다")


def _as_float(value: Any) -> float | None:
    """GBIS 좌표(숫자 또는 숫자 문자열) → float. 아니면 None."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip())
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 정류장 좌표 기준정보
# ---------------------------------------------------------------------------
def _station_items(record: Any) -> list[dict[str, Any]]:
    """기준정보 기록에서 정류장 목록을 꺼낸다. 형식이 다르면 빈 목록."""
    if not isinstance(record, Mapping) or record.get("ok") is not True:
        return []
    body = record.get("body")
    response = body.get("response") if isinstance(body, Mapping) else None
    msg_body = response.get("msgBody") if isinstance(response, Mapping) else None
    if not isinstance(msg_body, Mapping):
        return []
    return as_list(msg_body.get(GBIS_ROUTE_STATIONS.list_key))


def _turn_seq(items: list[dict[str, Any]]) -> int | None:
    """회차 순번: 항목의 turnSeq, 없으면 turnYn=Y 인 정류장의 순번."""
    for item in items:
        value = as_int(item.get("turnSeq"))
        if value is not None:
            return value
    for item in items:
        if item.get("turnYn") == "Y":
            return as_int(item.get("stationSeq"))
    return None


def parse_route_stations(record: Any) -> RouteStations | None:
    """getBusRouteStationListv2 기록 → RouteStations. 쓸 수 없는 기록이면 None.

    순번·ID·좌표가 없는 정류장 항목은 지도에 놓을 수 없으므로 뺀다.
    """
    items = _station_items(record)
    turn_seq = _turn_seq(items)
    if not items or turn_seq is None:
        return None
    stations: list[RouteStation] = []
    for item in items:
        seq = as_int(item.get("stationSeq"))
        station_id = as_id(item.get("stationId"))
        lat = _as_float(item.get("y"))
        lng = _as_float(item.get("x"))
        if seq is None or station_id is None or lat is None or lng is None:
            continue
        # "nan"·"inf" 문자열이나 범위 밖 값은 JSON 직렬화와 지도 표시를 깨므로 뺀다.
        if not (
            math.isfinite(lat) and math.isfinite(lng) and -90 <= lat <= 90 and -180 <= lng <= 180
        ):
            continue
        name = item.get("stationName")
        stations.append(
            RouteStation(
                station_seq=seq,
                station_id=station_id,
                name=name.strip() if isinstance(name, str) else "",
                lat=lat,
                lng=lng,
            )
        )
    if not stations:
        return None
    stations.sort(key=lambda s: s.station_seq)
    return RouteStations(turn_seq=turn_seq, stations=tuple(stations))


# ---------------------------------------------------------------------------
# 위치 기록(raw_poll.jsonl)
# ---------------------------------------------------------------------------
class ReverseLineReader:
    """파일을 끝에서부터 한 줄씩(바이트) 돌려준다. 빈 줄은 건너뛴다.

    - 열 때의 파일 크기까지만 읽는다(수집기가 그 뒤에 덧붙인 줄은 다음 조회에서 읽는다).
    - max_bytes 가 있으면 그만큼만 읽고 멈추며 hit_limit 을 True 로 둔다. 상한에 걸려 잘린
      줄 조각은 돌려주지 않는다.
    - UTF-8 에서 줄바꿈 바이트는 다른 글자에 섞이지 않으므로 바이트 단위로 나눠도 안전하다.
    """

    def __init__(
        self,
        path: Path,
        *,
        chunk_size: int = RAW_POLL_TAIL_CHUNK_BYTES,
        max_bytes: int | None = None,
    ) -> None:
        self._path = path
        self._chunk_size = chunk_size
        self._max_bytes = max_bytes
        self.hit_limit = False

    def __iter__(self) -> Iterator[bytes]:
        with self._path.open("rb") as fp:
            fp.seek(0, os.SEEK_END)
            position = fp.tell()
            bytes_read = 0
            remainder = b""
            while position > 0:
                read_size = min(self._chunk_size, position)
                if self._max_bytes is not None:
                    if bytes_read >= self._max_bytes:
                        self.hit_limit = True
                        return
                    read_size = min(read_size, self._max_bytes - bytes_read)
                position -= read_size
                bytes_read += read_size
                fp.seek(position)
                parts = (fp.read(read_size) + remainder).split(b"\n")
                remainder = parts[0]
                for part in reversed(parts[1:]):
                    if part.strip():
                        yield part
            if remainder.strip():
                yield remainder


def iter_lines_reversed(path: Path, chunk_size: int = RAW_POLL_TAIL_CHUNK_BYTES) -> Iterator[bytes]:
    """상한 없이 파일 끝에서부터 한 줄씩 읽는다(ReverseLineReader 참고)."""
    return iter(ReverseLineReader(path, chunk_size=chunk_size))


def _is_route_location_line(line: Any, route_id: str, include_trial: bool) -> bool:
    if not isinstance(line, dict) or line.get("api") != GBIS_BUS_LOCATION.api:
        return False
    params = line.get("params")
    if not isinstance(params, Mapping) or as_id(params.get("routeId")) != route_id:
        return False
    if line.get("ok") is not True:
        return False
    return include_trial or line.get("mode") != MODE_TRIAL


def location_items(line: Mapping[str, Any]) -> list[dict[str, Any]] | None:
    """위치 줄 body 의 차량 목록. 차량 위치로 쓸 수 없는 줄이면 None.

    - 결과 없음(resultCode 4)과 빈 응답(msgHeader·msgBody 없음, 수집기도 결과 없음으로 본다)은
      정상 0대([]).
    - 성공(resultCode 0)은 busLocationList 가 목록·객체여야 한다(빈 목록은 정상 0대).
    - body 가 객체가 아니거나, 결과 코드가 성공·결과 없음이 아니거나, 성공인데 목록을 꺼낼 수
      없으면 None. 이런 줄을 '차량 0대 최신'으로 쓰면 지도에서 차가 사라지므로 건너뛴다.
    """
    body = line.get("body")
    response = body.get("response") if isinstance(body, Mapping) else None
    if not isinstance(response, Mapping):
        return None
    header = response.get("msgHeader")
    if not isinstance(header, Mapping):
        is_empty_response = not any(k in response for k in ("msgHeader", "msgBody", "header"))
        return [] if is_empty_response else None
    code = normalize_result_code(header.get("resultCode"))
    if code in GBIS_NO_RESULT_CODES:
        return []
    if code not in GBIS_SUCCESS_CODES:
        return None
    msg_body = response.get("msgBody")
    if not isinstance(msg_body, Mapping):
        return None
    value = msg_body.get(GBIS_BUS_LOCATION.list_key)
    if not isinstance(value, list | Mapping):
        return None
    return as_list(value)


def find_latest_positions_in_file(
    path: Path,
    route_id: str,
    *,
    include_trial: bool,
    max_bytes: int | None = RAW_POLL_TAIL_MAX_BYTES_PER_FILE,
) -> LatestPositions | None:
    """raw_poll.jsonl 에서 그 노선의 마지막 '쓸 수 있는' 위치 줄. 없으면 None.

    가정: '같은 노선의 마지막 줄 = 가장 최근 수집'. 수집기는 노선마다 작업자 하나가 한 번에
    한 호출만 하고(타임아웃 < 주기), 응답을 받은 뒤 잠금 아래 줄을 덧붙인다
    (app/collector/collector.py, storage.append_jsonl). 그래서 같은 노선 줄은 파일에
    collected_at 순서로 쌓인다. 다른 노선 줄끼리는 순서가 섞여도 상관없다.

    깨진 줄(수집기가 쓰는 중인 마지막 줄 등)과 쓸 수 없는 줄(location_items 가 None)은
    건너뛰고 debug 로 수를 남긴다. 상한(max_bytes) 안에서 못 찾으면 warn 로그를 남기고 None.
    읽기 실패(OSError)는 그대로 올린다.
    """
    _require_route_id(route_id)
    api_marker = GBIS_BUS_LOCATION.api.encode("ascii")
    route_marker = route_id.encode("ascii")
    reader = ReverseLineReader(path, max_bytes=max_bytes)
    skipped_lines = 0
    try:
        for raw in reader:
            # 대부분의 줄은 다른 노선이므로 JSON 해석 전에 바이트로 먼저 거른다.
            if api_marker not in raw or route_marker not in raw:
                continue
            try:
                line = json.loads(raw)
            except ValueError:
                skipped_lines += 1
                continue
            if not _is_route_location_line(line, route_id, include_trial):
                continue
            items = location_items(line)
            if items is None:
                skipped_lines += 1
                continue
            try:
                # 차량 0대(결과 없음)도 정상 기록이라 수집 시각은 줄에서 직접 읽는다.
                collected_at = parse_collected_at(line.get("collected_at"))
                records, _ = extract_positions(line) if items else ([], 0)
            except ValueError:  # collected_at 이 없거나 시간대가 없다
                skipped_lines += 1
                continue
            return LatestPositions(
                collected_at=collected_at,
                records=tuple(r for r in records if r.route_id == route_id),
            )
        if reader.hit_limit:
            logger.warning(
                "route_positions_read_limit route_id=%s day=%s", route_id, path.parent.name
            )
        return None
    finally:
        if skipped_lines:
            logger.debug(
                "route_positions_skipped_lines route_id=%s day=%s count=%d",
                route_id,
                path.parent.name,
                skipped_lines,
            )


class RoutePositionRepository:
    def __init__(
        self, data_dir: Path, *, max_bytes_per_file: int = RAW_POLL_TAIL_MAX_BYTES_PER_FILE
    ) -> None:
        self._data_dir = data_dir
        self._max_bytes_per_file = max_bytes_per_file

    def load_route_stations(self, route_id: str) -> RouteStations | None:
        """가장 최근 날짜 기준정보의 노선 정류장 목록. 쓸 수 있는 기록이 없으면 None.

        가장 최근 날짜의 기록이 깨졌으면 그 전 날짜로 거슬러 간다.
        """
        _require_route_id(route_id)
        reference_root = self._data_dir / REFERENCE_DIRNAME
        if not reference_root.is_dir():
            return None
        day_dirs = sorted(
            (p for p in reference_root.iterdir() if p.is_dir() and _DATE_DIR_PATTERN.match(p.name)),
            key=lambda p: p.name,
            reverse=True,
        )
        pattern = f"*_{GBIS_ROUTE_STATIONS.api}_routeId-{route_id}{REFERENCE_RECORD_SUFFIX}"
        for day_dir in day_dirs:
            # 파일 이름 앞이 HHMMSS_순번이라 이름 역순이 최신순이다.
            for path in sorted(day_dir.glob(pattern), key=lambda p: p.name, reverse=True):
                try:
                    record = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError) as exc:
                    logger.warning(
                        "route_reference_unreadable route_id=%s day=%s error=%s",
                        route_id,
                        day_dir.name,
                        type(exc).__name__,
                    )
                    continue
                stations = parse_route_stations(record)
                if stations is not None:
                    return stations
                logger.warning(
                    "route_reference_unusable route_id=%s day=%s", route_id, day_dir.name
                )
        return None

    def find_latest_positions(
        self, route_id: str, today: date, *, lookback_days: int, include_trial: bool
    ) -> LatestPositions | None:
        """오늘부터 lookback_days 일 전까지 거슬러 가며 그 노선의 마지막 위치 기록을 찾는다.

        파일이 없거나, 파일당 읽기 상한 안에 노선 줄이 없거나, 읽다가 OSError 가 나면
        그 날짜는 '기록 없음'으로 보고 전날로 간다(읽기 실패는 warn 로그).
        """
        _require_route_id(route_id)
        for offset in range(lookback_days + 1):
            day = today - timedelta(days=offset)
            path = raw_poll_path(self._data_dir, day)
            if not path.is_file():
                continue
            try:
                found = find_latest_positions_in_file(
                    path,
                    route_id,
                    include_trial=include_trial,
                    max_bytes=self._max_bytes_per_file,
                )
            except OSError as exc:
                logger.warning(
                    "route_positions_unreadable route_id=%s day=%s error=%s",
                    route_id,
                    day.isoformat(),
                    type(exc).__name__,
                )
                continue
            if found is not None:
                return found
        return None
