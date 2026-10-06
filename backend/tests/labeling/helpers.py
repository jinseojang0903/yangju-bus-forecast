"""라벨 테스트 공용 도구. 합성 기록은 실제 위치 v2 픽스처
(tests/fixtures/gbis/getBusLocationListv2_routeId-235000092_*_real.json)의 항목 형식을 따른다."""

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.core.settings import KST
from app.labeling.records import PositionRecord
from app.labeling.trips import Trip, build_trips

G1300 = "235000092"
R1306 = "235000123"
BOARD_STATION_ID = "235000392"  # 덕현초교 잠실행. G1300 순번 13, 1306 순번 11
VEH_A = "235000359"


def at(hour: int, minute: int, second: int = 0) -> datetime:
    return datetime(2026, 10, 6, hour, minute, second, tzinfo=KST)


def pos(
    moment: datetime,
    seq: int,
    state: int | None,
    seat: int | None,
    *,
    route_id: str = G1300,
    veh_id: str = VEH_A,
    station_id: str | None = None,
    mode: str | None = "run",
    interval_sec: int | None = 60,
    is_holiday: bool = False,
) -> PositionRecord:
    return PositionRecord(
        collected_at=moment,
        route_id=route_id,
        veh_id=veh_id,
        station_seq=seq,
        station_id=station_id or str(277000000 + seq),
        state_cd=state,
        remain_seat_cnt=seat,
        plate_no=f"경기76바{veh_id[-4:]}",
        route_type_cd="11",
        mode=mode,
        interval_sec=interval_sec,
        is_holiday=is_holiday,
    )


def single_trip(*records: PositionRecord) -> Trip:
    trips = build_trips(records)
    assert len(trips) == 1, trips
    return trips[0]


def gbis_item(
    veh_id: str,
    seq: int,
    state: int,
    seat: int,
    *,
    route_id: str,
    station_id: str | None = None,
) -> dict[str, Any]:
    """위치 v2 응답 항목. 실제 응답처럼 숫자 필드는 숫자로 둔다."""
    return {
        "crowded": 1,
        "lowPlate": 0,
        "plateNo": f"경기76바{veh_id[-4:]}",
        "remainSeatCnt": seat,
        "routeId": int(route_id),
        "routeTypeCd": 11,
        "stateCd": state,
        "stationId": int(station_id or 277000000 + seq),
        "stationSeq": seq,
        "taglessCd": 1,
        "vehId": int(veh_id),
    }


def location_line(
    moment: datetime,
    items: list[dict[str, Any]],
    *,
    route_id: str,
    mode: str = "run",
    interval_sec: int | None = 60,
    is_holiday: bool = False,
    ok: bool = True,
) -> dict[str, Any]:
    """app/collector/storage.build_record 와 같은 형식의 JSONL 한 줄."""
    line: dict[str, Any] = {
        "collected_at": moment.isoformat(timespec="milliseconds"),
        "api": "getBusLocationListv2",
        "params": {"routeId": route_id, "format": "json"},
        "http_status": 200,
        "elapsed_ms": 100,
        "ok": ok,
        "result_code": "0" if ok else "99",
        "result_message": "정상적으로 처리되었습니다." if ok else "오류",
        "error": None if ok else "resultCode=99 오류",
        "is_weekday": True,
        "is_holiday": is_holiday,
        "mode": mode,
        "interval_sec": interval_sec,
        "body": {
            "response": {
                "comMsgHeader": "",
                "msgHeader": {"resultCode": 0 if ok else 99},
                "msgBody": {"busLocationList": items},
            }
        },
    }
    return line


def arrival_line(moment: datetime) -> dict[str, Any]:
    return {
        "collected_at": moment.isoformat(timespec="milliseconds"),
        "api": "getBusArrivalListv2",
        "params": {"stationId": BOARD_STATION_ID, "format": "json"},
        "ok": True,
        "mode": "run",
        "interval_sec": 60,
        "body": {"response": {"msgBody": {"busArrivalList": []}}},
    }


def write_jsonl(path: Path, lines: list[dict[str, Any] | str]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(
        (line if isinstance(line, str) else json.dumps(line, ensure_ascii=False)) + "\n"
        for line in lines
    )
    path.write_text(text, encoding="utf-8")
    return path
