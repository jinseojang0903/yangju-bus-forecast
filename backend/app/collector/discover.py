"""routeId·stationId 찾기. 결과는 화면에만 출력하고 파일에 쓰지 않는다.

메인이 출력 근거(앞·뒤 정류장, 회차 지점 기준 방향, 하차 정류장 순번)를 보고
settings.COLLECT_TARGET 을 채운다.
"""

from collections.abc import Callable
from typing import Any

from app.collector.gbis import CallResult, GbisClient
from app.core.settings import (
    COLLECT_TARGET,
    GBIS_ROUTE_INFO,
    GBIS_ROUTE_LIST,
    GBIS_ROUTE_STATIONS,
    CollectTarget,
    TargetRoute,
)

# 키워드 검색이 부분 일치라 후보가 많을 수 있다.
# 정확히 같은 번호가 없을 때만 이만큼 살펴본다.
_MAX_FALLBACK_CANDIDATES = 3
_ROUTE_FIELDS = (
    "routeId",
    "routeName",
    "routeTypeName",
    "routeTypeCd",
    "regionName",
    "districtCd",
    "adminName",
    "startStationName",
    "endStationName",
)
_ROUTE_INFO_FIELDS = (
    "startStationId",
    "startStationName",
    "endStationId",
    "endStationName",
    "regionName",
    "companyName",
    "upFirstTime",
    "upLastTime",
)

Printer = Callable[[str], None]


def _get(item: dict[str, Any], key: str) -> str:
    value = item.get(key)
    return "" if value is None else str(value).strip()


def _seq(item: dict[str, Any]) -> int:
    try:
        return int(_get(item, "stationSeq"))
    except ValueError:
        return -1


def _fields(item: dict[str, Any], keys: tuple[str, ...]) -> str:
    present = [f"{k}={_get(item, k)}" for k in keys if _get(item, k)]
    # 회차 정보는 이름이 확실하지 않아 'turn' 이 들어간 키를 모두 보여 준다.
    present += [f"{k}={v}" for k, v in item.items() if "turn" in k.lower() and k not in keys]
    return " ".join(present) if present else "(필드 없음)"


def _call_line(result: CallResult) -> str:
    return (
        f"  [{result.api}] params={result.params} http={result.http_status} ok={result.ok} "
        f"code={result.result_code} msg={result.result_message} items={len(result.items)}"
        + (f" error={result.error}" if result.error else "")
    )


def _describe_direction(seq: int, turn_seq: int | None) -> str:
    if turn_seq is None:
        return "회차 정보 없음"
    if seq < turn_seq:
        return f"회차(순번 {turn_seq}) 전: 기점→회차 방향"
    if seq == turn_seq:
        return "회차 지점 자체"
    return f"회차(순번 {turn_seq}) 후: 회차→종점(기점) 방향"


def _report_stations(
    stations: list[dict[str, Any]], target: CollectTarget, route: TargetRoute, out: Printer
) -> None:
    ordered = sorted(stations, key=_seq)
    turn_seq = next((_seq(s) for s in ordered if _get(s, "turnYn").upper() == "Y"), None)
    out(f"  정류장 수={len(ordered)} 회차 순번={turn_seq if turn_seq is not None else '없음'}")

    alights = [s for s in ordered if route.alight_station_name in _get(s, "stationName")]
    out(f"  하차 정류장 '{route.alight_station_name}' 후보 {len(alights)}개")
    for s in alights:
        out(
            f"    - stationId={_get(s, 'stationId')} stationSeq={_seq(s)} "
            f"name={_get(s, 'stationName')} {_describe_direction(_seq(s), turn_seq)}"
        )

    boards = [
        (i, s) for i, s in enumerate(ordered) if target.board_station_name in _get(s, "stationName")
    ]
    out(f"  승차 정류장 '{target.board_station_name}' 후보 {len(boards)}개")
    for index, s in boards:
        seq = _seq(s)
        prev_name = _get(ordered[index - 1], "stationName") if index > 0 else "(기점)"
        next_name = (
            _get(ordered[index + 1], "stationName") if index + 1 < len(ordered) else "(종점)"
        )
        later_alights = [_seq(a) for a in alights if _seq(a) > seq]
        toward = (
            f"뒤에 하차 정류장 있음(순번 {later_alights[0]}) → {target.direction_label} 후보"
            if later_alights
            else "뒤에 하차 정류장 없음 → 반대 방향일 가능성"
        )
        out(
            f"    - stationId={_get(s, 'stationId')} stationSeq={seq} "
            f"mobileNo={_get(s, 'mobileNo')} name={_get(s, 'stationName')}"
        )
        out(f"      앞={prev_name} / 뒤={next_name}")
        out(f"      {_describe_direction(seq, turn_seq)}; {toward}")


def discover(
    client: GbisClient, printer: Printer = print, target: CollectTarget = COLLECT_TARGET
) -> int:
    """노선별 routeId 후보와 덕현초교·하차 정류장 후보를 근거와 함께 출력한다.

    호출 실패가 하나라도 있으면 1, 아니면 0 을 돌려준다.
    """

    # 응답 필드를 그대로 찍으므로, 키가 되돌아오는 드문 경우에 대비해 모든 줄을 가린다.
    def out(line: str) -> None:
        printer(client.redactor.redact(line))

    exit_code = 0
    for route in target.routes:
        out(f"=== 노선 {route.route_name} (하차: {route.alight_station_name}) ===")
        listed = client.call(GBIS_ROUTE_LIST, {"keyword": route.route_name})
        out(_call_line(listed))
        if not listed.ok:
            exit_code = 1
            continue
        exact = [r for r in listed.items if _get(r, "routeName") == route.route_name]
        out(f"  노선 후보 {len(listed.items)}개, 번호 정확히 일치 {len(exact)}개")
        for r in listed.items:
            mark = "*" if r in exact else " "
            out(f"   {mark} {_fields(r, _ROUTE_FIELDS)}")
        candidates = exact or listed.items[:_MAX_FALLBACK_CANDIDATES]

        for candidate in candidates:
            route_id = _get(candidate, "routeId")
            if not route_id:
                continue
            out(f"--- routeId={route_id} ({_get(candidate, 'routeName')}) ---")
            info = client.call(GBIS_ROUTE_INFO, {"routeId": route_id})
            out(_call_line(info))
            for item in info.items:
                out(f"  노선 정보: {_fields(item, _ROUTE_INFO_FIELDS)}")
            stations = client.call(GBIS_ROUTE_STATIONS, {"routeId": route_id})
            out(_call_line(stations))
            if not (info.ok and stations.ok):
                exit_code = 1
            if stations.ok:
                _report_stations(stations.items, target, route, out)
    out("")
    out("다음: 근거를 확인한 뒤 backend/app/core/settings.py 의 COLLECT_TARGET 에")
    out("      routes[].route_id 와 board_station_id(덕현초교 잠실행)를 채운다.")
    return exit_code
