"""JSONL 한 줄(수집기 build_record)을 DB 행(raw_poll·bus_position·bus_arrival)으로 바꾼다.

열 정의와 CHECK 의 기준은 backend/migrations/0001_init.sql 이다. GBIS 값은 라벨
(app/labeling/records)과 같은 해석 함수(as_int·as_id·parse_collected_at)로 읽는다.
GBIS 원값 열은 NULL 을 허용하므로, 값이 비었거나 열 타입 범위를 벗어나면 NULL 로 두고
행은 남긴다(적재 실패로 기록을 잃지 않게).
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Final

from app.collector.collector import MODE_ONCE, MODE_RUN, MODE_TRIAL
from app.collector.gbis import normalize_result_code
from app.collector.schedule import is_holiday as is_korean_holiday
from app.core.settings import (
    COLLECT_TARGET,
    GBIS_BUS_ARRIVAL,
    GBIS_BUS_LOCATION,
    SERVICE_HOURS,
    CollectTarget,
)
from app.labeling.records import as_id, as_int, line_is_holiday, parse_collected_at

# raw_poll.mode CHECK 와 같은 값. 이 밖의 mode 줄은 넣지 않고 센다.
ALLOWED_MODES: Final = frozenset({MODE_RUN, MODE_ONCE, MODE_TRIAL})
# 0001_init.sql 의 CHECK 와 같은 형식(raw_poll.result_code, raw_poll.jsonl_file).
RESULT_CODE_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{1,64}")
JSONL_FILE_PATTERN: Final = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}/[a-z0-9_]{1,64}\.jsonl")
ARRIVAL_RANKS: Final = (1, 2)
# 도착 순위 행을 만들지 판단하는 차량 필드(끝에 순위 1·2 를 붙인다).
# 하나라도 값이 있으면 행을 만든다(0001_init.sql bus_arrival 주석).
# remainSeatCnt·crowded 는 차가 없어도 0 으로 오므로(실데이터 2026-10-07) 판단에 쓰지 않는다.
ARRIVAL_VEHICLE_FIELDS: Final = ("vehId", "plateNo", "predictTimeSec", "predictTime")

_INT2_RANGE: Final = (-(2**15), 2**15 - 1)
_INT4_RANGE: Final = (-(2**31), 2**31 - 1)


# ---------------------------------------------------------------------------
# 값 변환
# ---------------------------------------------------------------------------
def _ranged(value: Any, bounds: tuple[int, int]) -> int | None:
    try:
        number = as_int(value)
    except ValueError:  # 아주 긴 숫자 문자열(int 자릿수 상한 초과)
        return None
    if number is None or not bounds[0] <= number <= bounds[1]:
        return None
    return number


def int2(value: Any) -> int | None:
    """smallint 열 값. 정수가 아니거나 범위를 벗어나면 None."""
    return _ranged(value, _INT2_RANGE)


def int4(value: Any) -> int | None:
    """integer 열 값. 정수가 아니거나 범위를 벗어나면 None."""
    return _ranged(value, _INT4_RANGE)


def _storable(value: str) -> bool:
    """PostgreSQL text(UTF-8)에 넣을 수 있는지. NUL 과 짝 없는 서로게이트("\\ud800")는 거부된다."""
    if "\x00" in value:
        return False
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def text(value: Any) -> str | None:
    """text 열 값. 문자열이 아니거나 비었거나 DB 에 넣을 수 없는 글자가 있으면 None."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    if not stripped or not _storable(stripped):
        return None
    return stripped


def id_text(value: Any) -> str | None:
    """GBIS ID 를 text 열 값으로(as_id 와 같은 해석, DB 에 넣을 수 없는 글자가 있으면 None)."""
    ident = as_id(value)
    if ident is None or not _storable(ident):
        return None
    return ident


def db_result_code(value: Any) -> str | None:
    """결과 코드를 수집기 규칙(normalize_result_code)으로 맞춘다.

    raw_poll CHECK 형식이 아니면 None.
    """
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    try:
        code = normalize_result_code(value)
    except ValueError:  # '²' 같은 유니코드 숫자는 isdigit 이지만 int() 가 거부한다
        return None
    if code is None or not RESULT_CODE_PATTERN.fullmatch(code):
        return None
    return code


def jsonl_file_name(day: date, filename: str) -> str:
    """raw_poll.jsonl_file 값(데이터 폴더 기준 상대 경로). CHECK 형식이 아니면 ValueError."""
    relative = f"{day.isoformat()}/{filename}"
    if not JSONL_FILE_PATTERN.fullmatch(relative):
        raise ValueError("jsonl_file 형식이 raw_poll CHECK 와 맞지 않는다")
    return relative


def indexed_items(value: Any) -> list[tuple[int, dict[str, Any]]]:
    """GBIS 목록을 (응답 안 순서, 항목)으로. 1건이면 dict 로 온다(as_list 와 같은 해석).

    dict 가 아닌 항목은 건너뛰되 순서 번호는 원본 목록 기준으로 남긴다(JSONL 로 되짚어 가려고).
    """
    if isinstance(value, dict):
        return [(0, value)]
    if isinstance(value, list):
        return [(i, v) for i, v in enumerate(value) if isinstance(v, dict)]
    return []


def _response_list_value(body: Any, list_key: str) -> Any:
    """body.response.msgBody.<list_key>. 결과 없음·빈 응답·구조가 다르면 None."""
    if not isinstance(body, Mapping):
        return None
    response = body.get("response")
    if not isinstance(response, Mapping):
        return None
    msg_body = response.get("msgBody")
    if not isinstance(msg_body, Mapping):
        return None
    return msg_body.get(list_key)


# ---------------------------------------------------------------------------
# 행
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RawPollRow:
    line_no: int
    collected_at: datetime
    api: str
    route_id: str | None
    station_id: str | None
    ok: bool
    http_status: int | None
    result_code: str | None
    mode: str
    interval_sec: int | None
    is_holiday: bool


@dataclass(frozen=True)
class BusPositionRow:
    line_no: int  # raw_poll 을 넣은 뒤 raw_poll_id 로 바꾼다
    item_index: int
    collected_at: datetime
    route_id: str
    veh_id: str | None
    plate_no: str | None
    station_seq: int | None
    station_id: str | None
    state_cd: int | None
    remain_seat_cnt: int | None  # 원값 그대로(-1 포함). 해석은 라벨에서
    crowded: int | None
    interval_sec: int | None
    mode: str
    is_holiday: bool


@dataclass(frozen=True)
class BusArrivalRow:
    line_no: int
    route_id: str
    arrival_rank: int
    item_index: int
    collected_at: datetime
    station_id: str | None
    veh_id: str | None
    plate_no: str | None
    predict_time_sec: int | None
    predict_time_min: int | None
    remain_seat_cnt: int | None
    location_no: int | None
    sta_order: int | None
    crowded: int | None
    flag: str | None


@dataclass(frozen=True)
class ServiceDayRow:
    service_date: date
    is_weekday: bool
    is_holiday: bool
    operation_kind: str  # regular | trial | none


@dataclass(frozen=True)
class ParsedLine:
    """대상 줄 하나에서 만든 행들."""

    target_name: str  # 요약용: 노선 이름(G1300·1306) 또는 '도착'
    raw_poll: RawPollRow
    positions: tuple[BusPositionRow, ...] = ()
    arrivals: tuple[BusArrivalRow, ...] = ()
    arrival_empty_ranks: int = 0  # 차 정보가 비어 행을 만들지 않은 순위 수
    arrival_other_route_items: int = 0  # 대상 노선이 아니라 넣지 않은 도착 항목 수


# ---------------------------------------------------------------------------
# 대상
# ---------------------------------------------------------------------------
ARRIVAL_TARGET_NAME: Final = "도착"


@dataclass(frozen=True)
class LoadTargets:
    """DB 적재 대상: 위치 API 의 라벨 대상 노선과 도착 API 의 승차 정류장."""

    location_routes: Mapping[str, str]  # routeId → 노선 이름
    arrival_station_id: str

    @classmethod
    def from_settings(cls, target: CollectTarget = COLLECT_TARGET) -> "LoadTargets":
        """settings 의 라벨 대상 노선(G1300·1306)과 덕현초교. ID 가 비어 있으면 ValueError."""
        if not target.board_station_id:
            raise ValueError("COLLECT_TARGET.board_station_id 가 없다")
        routes = {r.route_id: r.route_name for r in target.routes if r.label_target and r.route_id}
        if not routes:
            raise ValueError("라벨 대상 노선의 route_id 가 없다")
        return cls(location_routes=routes, arrival_station_id=target.board_station_id)

    def match(self, line: Mapping[str, Any]) -> str | None:
        """대상 줄이면 요약 이름(노선 이름 또는 '도착'), 아니면 None."""
        params = line.get("params")
        if not isinstance(params, Mapping):
            return None
        api = line.get("api")
        if api == GBIS_BUS_LOCATION.api:
            return self.location_routes.get(as_id(params.get("routeId")) or "")
        if (
            api == GBIS_BUS_ARRIVAL.api
            and as_id(params.get("stationId")) == self.arrival_station_id
        ):
            return ARRIVAL_TARGET_NAME
        return None


def line_mode(line: Mapping[str, Any]) -> str | None:
    """raw_poll.mode 로 쓸 수 있는 mode. CHECK 밖이면 None."""
    mode = line.get("mode")
    return mode if isinstance(mode, str) and mode in ALLOWED_MODES else None


# ---------------------------------------------------------------------------
# 변환
# ---------------------------------------------------------------------------
def build_raw_poll(line: Mapping[str, Any], line_no: int, mode: str) -> RawPollRow:
    """대상 줄의 raw_poll 행. collected_at 이 잘못되면 ValueError(깨진 줄로 센다)."""
    collected_at = parse_collected_at(line.get("collected_at"))
    params = line.get("params")
    params = params if isinstance(params, Mapping) else {}
    is_location = line.get("api") == GBIS_BUS_LOCATION.api
    interval_sec = int4(line.get("interval_sec"))
    return RawPollRow(
        line_no=line_no,
        collected_at=collected_at,
        api=GBIS_BUS_LOCATION.api if is_location else GBIS_BUS_ARRIVAL.api,
        route_id=id_text(params.get("routeId")) if is_location else None,
        station_id=None if is_location else id_text(params.get("stationId")),
        ok=line.get("ok") is True,
        http_status=int4(line.get("http_status")),
        result_code=db_result_code(line.get("result_code")),
        mode=mode,
        interval_sec=interval_sec if interval_sec is not None and interval_sec > 0 else None,
        # 수집 당시 플래그를 그대로 둔다(원본 보존). 날짜 분류의 기준은 service_day.is_holiday.
        is_holiday=line_is_holiday(line, collected_at),
    )


def build_positions(line: Mapping[str, Any], raw: RawPollRow) -> list[BusPositionRow]:
    """위치 응답의 차량 항목마다 1행. ok 가 아닌 줄·결과 없음·빈 응답이면 []."""
    if not raw.ok:
        return []
    rows: list[BusPositionRow] = []
    for index, item in indexed_items(
        _response_list_value(line.get("body"), GBIS_BUS_LOCATION.list_key)
    ):
        if index > _INT2_RANGE[1]:
            break
        rows.append(
            BusPositionRow(
                line_no=raw.line_no,
                item_index=index,
                collected_at=raw.collected_at,
                # 대상 줄이므로 raw.route_id 는 있다. 항목 routeId 가 비면 요청 노선을 쓴다.
                route_id=id_text(item.get("routeId")) or raw.route_id or "",
                veh_id=id_text(item.get("vehId")),
                plate_no=text(item.get("plateNo")),
                station_seq=int4(item.get("stationSeq")),
                station_id=id_text(item.get("stationId")),
                state_cd=int2(item.get("stateCd")),
                remain_seat_cnt=int4(item.get("remainSeatCnt")),
                crowded=int2(item.get("crowded")),
                interval_sec=raw.interval_sec,
                mode=raw.mode,
                is_holiday=raw.is_holiday,
            )
        )
    return rows


def _has_vehicle(item: Mapping[str, Any], rank: int) -> bool:
    # as_id: 빈 문자열·None 은 None, 숫자·글자는 값으로 본다.
    return any(as_id(item.get(f"{name}{rank}")) is not None for name in ARRIVAL_VEHICLE_FIELDS)


def build_arrivals(
    line: Mapping[str, Any], raw: RawPollRow, route_ids: Mapping[str, str]
) -> tuple[list[BusArrivalRow], int, int]:
    """도착 응답의 대상 노선 항목 × 순위(1·2)마다 1행.

    돌려주는 값: (행, 차 정보가 비어 건너뛴 순위 수, 대상 노선이 아닌 항목 수).
    ok 가 아닌 줄·결과 없음·빈 응답이면 ([], 0, 0).
    """
    if not raw.ok:
        return [], 0, 0
    rows: list[BusArrivalRow] = []
    empty_ranks = 0
    other_items = 0
    for index, item in indexed_items(
        _response_list_value(line.get("body"), GBIS_BUS_ARRIVAL.list_key)
    ):
        route_id = id_text(item.get("routeId"))
        if route_id is None or route_id not in route_ids or index > _INT2_RANGE[1]:
            other_items += 1
            continue
        for rank in ARRIVAL_RANKS:
            if not _has_vehicle(item, rank):
                empty_ranks += 1
                continue
            rows.append(
                BusArrivalRow(
                    line_no=raw.line_no,
                    route_id=route_id,
                    arrival_rank=rank,
                    item_index=index,
                    collected_at=raw.collected_at,
                    station_id=id_text(item.get("stationId")) or raw.station_id,
                    veh_id=id_text(item.get(f"vehId{rank}")),
                    plate_no=text(item.get(f"plateNo{rank}")),
                    predict_time_sec=int4(item.get(f"predictTimeSec{rank}")),
                    predict_time_min=int4(item.get(f"predictTime{rank}")),
                    remain_seat_cnt=int4(item.get(f"remainSeatCnt{rank}")),
                    location_no=int4(item.get(f"locationNo{rank}")),
                    sta_order=int4(item.get("staOrder")),
                    crowded=int2(item.get(f"crowded{rank}")),
                    flag=text(item.get("flag")),
                )
            )
    return rows, empty_ranks, other_items


def build_line(
    line: Mapping[str, Any], line_no: int, mode: str, target_name: str, targets: LoadTargets
) -> ParsedLine:
    """대상 줄 하나의 행들. collected_at 이 잘못되면 ValueError."""
    raw = build_raw_poll(line, line_no, mode)
    if raw.api == GBIS_BUS_LOCATION.api:
        return ParsedLine(target_name, raw, positions=tuple(build_positions(line, raw)))
    arrivals, empty_ranks, other_items = build_arrivals(line, raw, targets.location_routes)
    return ParsedLine(
        target_name,
        raw,
        arrivals=tuple(arrivals),
        arrival_empty_ranks=empty_ranks,
        arrival_other_route_items=other_items,
    )


def operation_kind(modes: Mapping[str, int]) -> str:
    """그날 줄의 mode 로 운영 구분.

    정식 수집(run) 줄이 있으면 regular. once·trial 만 있는 날은 시험 호출일이라 trial.
    줄이 없으면 none.
    """
    if modes.get(MODE_RUN, 0):
        return "regular"
    if modes.get(MODE_ONCE, 0) or modes.get(MODE_TRIAL, 0):
        return "trial"
    return "none"


def is_service_holiday(day: date) -> bool:
    """날짜 분류(service_day.is_holiday)의 공휴일 판정.

    공휴일 라이브러리(수집기가 JSONL 에 쓰는 판정)와 설정 공휴일 목록(SERVICE_HOURS.holidays) 중
    하나라도 해당하면 true 다(service_day.is_holiday 주석: 기준은 설정 모듈).
    raw_poll·bus_position 의 is_holiday 는 수집 당시 플래그 그대로이고, 사례·평가 대상일은 이
    값(service_day)으로 고른다.
    """
    return is_korean_holiday(day) or day in SERVICE_HOURS.holidays


def build_service_day(day: date, modes: Mapping[str, int]) -> ServiceDayRow:
    """운행일 행. is_weekday 는 service_day CHECK(isodow < 6)와 같은 식으로 정한다."""
    return ServiceDayRow(
        service_date=day,
        is_weekday=day.isoweekday() < 6,
        is_holiday=is_service_holiday(day),
        operation_kind=operation_kind(modes),
    )
