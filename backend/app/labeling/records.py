"""수집기 JSONL(raw_poll.jsonl)에서 위치 기록을 꺼낸다.

입력 줄 형식은 app/collector/storage.py 의 build_record 다. 위치 API(getBusLocationListv2) 이고
ok 인 줄의 body.response.msgBody.busLocationList 항목마다 PositionRecord 1개를 만든다.
GBIS 는 숫자 필드를 숫자 또는 문자열로 보내므로 관대하게 읽는다.
"""

import json
import re
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from app.collector.gbis import as_list
from app.collector.schedule import is_holiday as is_korean_holiday
from app.core.settings import GBIS_BUS_LOCATION, KST, MODE_TRIAL

_INT_PATTERN = re.compile(r"-?\d+")


@dataclass(frozen=True)
class PositionRecord:
    """위치 응답의 차량 1대 1회 관측."""

    collected_at: datetime  # 수집 시각(KST)
    route_id: str
    veh_id: str
    station_seq: int
    station_id: str | None
    state_cd: int | None
    remain_seat_cnt: int | None  # -1 은 정보 없음
    plate_no: str | None
    route_type_cd: str | None
    mode: str | None
    interval_sec: int | None  # 수집 간격. 이전 형식 줄(once 등)에는 없다
    is_holiday: bool

    @property
    def is_trial(self) -> bool:
        return self.mode == MODE_TRIAL


@dataclass
class ReadStats:
    """입력 파일 읽기 집계. 리포트에 그대로 보여 준다(조용히 버리는 줄이 없게)."""

    lines: int = 0
    broken_lines: int = 0  # JSON 이 아니거나 collected_at 이 없거나 시간대가 없는 줄
    location_lines: int = 0
    failed_location_lines: int = 0  # ok=false 이거나 body 가 JSON 이 아닌 위치 줄
    trial_lines_skipped: int = 0
    items: int = 0
    skipped_items: int = 0  # vehId·stationSeq·routeId 가 없는 항목
    interval_secs: set[int | None] = field(default_factory=set)


def as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _INT_PATTERN.fullmatch(value.strip()):
        return int(value.strip())
    return None


def as_id(value: Any) -> str | None:
    """GBIS ID(숫자 또는 문자열) → 앞뒤 공백을 뺀 문자열. 없거나 빈 값·불리언이면 None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def parse_collected_at(value: Any) -> datetime:
    """ISO 8601 시각을 KST 로 바꾼다. 형식이 틀리거나 시간대가 없으면 ValueError."""
    if not isinstance(value, str):
        raise ValueError("collected_at 이 문자열이 아니다")
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        raise ValueError("collected_at 에 시간대가 없다")
    return moment.astimezone(KST)


def _location_items(body: Any) -> list[dict[str, Any]] | None:
    """body 에서 차량 목록을 꺼낸다. body 구조가 다르면 None."""
    if not isinstance(body, Mapping):
        return None
    response = body.get("response")
    if not isinstance(response, Mapping):
        return None
    msg_body = response.get("msgBody")
    if msg_body is None or msg_body == "":
        return []  # 결과 없음(차가 없음)
    if not isinstance(msg_body, Mapping):
        return None
    return as_list(msg_body.get(GBIS_BUS_LOCATION.list_key))


def extract_positions(line: Mapping[str, Any]) -> tuple[list[PositionRecord], int]:
    """JSONL 한 줄(dict)에서 위치 기록을 꺼낸다. 돌려주는 값: (기록 목록, 건너뛴 항목 수).

    위치 API 줄이 아니면 ([], 0). collected_at 이 잘못되면 ValueError.
    """
    if line.get("api") != GBIS_BUS_LOCATION.api:
        return [], 0
    collected_at = parse_collected_at(line.get("collected_at"))
    items = _location_items(line.get("body")) if line.get("ok") is True else None
    if items is None:
        return [], 0

    params = line.get("params")
    param_route_id = as_id(params.get("routeId")) if isinstance(params, Mapping) else None
    holiday_flag = line.get("is_holiday")
    is_holiday = (
        holiday_flag if isinstance(holiday_flag, bool) else is_korean_holiday(collected_at.date())
    )
    mode = line.get("mode") if isinstance(line.get("mode"), str) else None
    interval_sec = as_int(line.get("interval_sec"))

    records: list[PositionRecord] = []
    skipped = 0
    for item in items:
        route_id = as_id(item.get("routeId")) or param_route_id
        veh_id = as_id(item.get("vehId"))
        station_seq = as_int(item.get("stationSeq"))
        if route_id is None or veh_id is None or station_seq is None:
            skipped += 1
            continue
        plate_no = item.get("plateNo")
        records.append(
            PositionRecord(
                collected_at=collected_at,
                route_id=route_id,
                veh_id=veh_id,
                station_seq=station_seq,
                station_id=as_id(item.get("stationId")),
                state_cd=as_int(item.get("stateCd")),
                remain_seat_cnt=as_int(item.get("remainSeatCnt")),
                plate_no=plate_no if isinstance(plate_no, str) else None,
                route_type_cd=as_id(item.get("routeTypeCd")),
                mode=mode,
                interval_sec=interval_sec,
                is_holiday=is_holiday,
            )
        )
    return records, skipped


def _iter_lines(path: Path) -> Iterator[str]:
    with path.open("r", encoding="utf-8") as fp:
        for raw in fp:
            text = raw.strip()
            if text:
                yield text


def load_raw_poll(path: Path, *, include_trial: bool) -> tuple[list[PositionRecord], ReadStats]:
    """raw_poll.jsonl 을 읽어 위치 기록과 읽기 집계를 돌려준다.

    include_trial 이 False 면 mode=trial 줄을 뺀다(뺀 줄 수는 집계에 남긴다).
    깨진 줄(수집기가 줄 중간에 죽은 경우 등)은 건너뛰고 broken_lines 로 센다.
    파일이 없으면 FileNotFoundError.
    """
    stats = ReadStats()
    records: list[PositionRecord] = []
    for text in _iter_lines(path):
        stats.lines += 1
        try:
            line = json.loads(text)
        except ValueError:
            stats.broken_lines += 1
            continue
        if not isinstance(line, dict) or line.get("api") != GBIS_BUS_LOCATION.api:
            continue
        stats.location_lines += 1
        if not include_trial and line.get("mode") == MODE_TRIAL:
            stats.trial_lines_skipped += 1
            continue
        if line.get("ok") is not True or _location_items(line.get("body")) is None:
            stats.failed_location_lines += 1
            continue
        try:
            found, skipped = extract_positions(line)
        except ValueError:
            stats.broken_lines += 1
            continue
        stats.interval_secs.add(as_int(line.get("interval_sec")))
        stats.items += len(found) + skipped
        stats.skipped_items += skipped
        records.extend(found)
    return records, stats
