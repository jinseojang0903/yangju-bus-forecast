"""기준정보 찾기(discover): 노선 routeId, 덕현초교·하차 정류장의 stationId 와 순번.

노선 API 는 사용자 규칙에 따라 하루 4회 이하로만 부른다
(노선마다 검색 1 + 정류장 목록 1).
- 노선 상세(getBusRouteInfoItemv2)는 부르지 않는다.
  방향은 정류장 목록의 순번·turnYn 으로 본다.
- 검색 결과가 여럿이면 추가 호출 없이 검색 결과 필드만으로 하나를 고른다.
  못 고르면 정류장 목록을 부르지 않고 후보를 출력한 뒤 멈춘다(종료 코드 2).
- 호출마다 원본 기록(키 가림)을 <데이터폴더>/reference/<날짜>/ 에 저장하고,
  고른 결과를 같은 폴더의 targets.json 에 쓴다.
메인이 출력과 targets.json 을 보고 settings.COLLECT_TARGET 을 채운다.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from app.collector.gbis import CallResult, GbisClient
from app.collector.schedule import Clock, to_kst
from app.collector.status import StatusStore
from app.collector.storage import build_record, reference_dir, write_json_atomic
from app.core.settings import (
    COLLECT_TARGET,
    GBIS_ROUTE_LIST,
    GBIS_ROUTE_STATIONS,
    REFERENCE_RECORD_SUFFIX,
    TARGETS_FILENAME,
    CollectTarget,
    GbisEndpoint,
    TargetRoute,
)

EXIT_OK = 0
EXIT_CALL_FAILED = 1
EXIT_NEEDS_DECISION = 2  # 후보를 못 골랐거나, 노선 API 하루 상한 때문에 멈춤

MODE_DISCOVER = "discover"
CALLS_PER_ROUTE = 2  # 검색 1 + 정류장 목록 1

_ROUTE_FIELDS = (
    "routeId",
    "routeName",
    "routeTypeName",
    "routeTypeCd",
    "regionName",
    "adminName",
    "districtCd",
    "startStationName",
    "endStationName",
)
_REGION_FIELDS = ("regionName", "adminName")
_TERMINAL_FIELDS = ("startStationName", "endStationName")

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


# ---------------------------------------------------------------------------
# 노선 고르기 (추가 호출 없이 검색 결과 필드만 쓴다)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class RouteChoice:
    chosen: dict[str, Any] | None
    reason: str


def choose_route(
    items: list[dict[str, Any]], route: TargetRoute, target: CollectTarget
) -> RouteChoice:
    """routeName 정확히 일치 + (지역에 '양주' 또는 기점·종점에 '잠실')인 후보가 하나면 그것.

    번호 일치가 하나뿐이고 지역·기점·종점 필드가 아예 없으면(확인할 정보가 없음) 그것을 고른다.
    그 밖에는 고르지 않는다(chosen=None).
    """
    exact = [r for r in items if _get(r, "routeName") == route.route_name]

    def matches(item: dict[str, Any]) -> bool:
        in_region = any(target.region_keyword in _get(item, f) for f in _REGION_FIELDS)
        at_terminal = any(target.destination_name in _get(item, f) for f in _TERMINAL_FIELDS)
        return in_region or at_terminal

    strict = [r for r in exact if matches(r)]
    if len(strict) == 1 and _get(strict[0], "routeId"):
        return RouteChoice(strict[0], "번호 일치 + 지역 또는 기점·종점 일치")
    if not strict and len(exact) == 1 and _get(exact[0], "routeId"):
        has_info = any(_get(exact[0], f) for f in _REGION_FIELDS + _TERMINAL_FIELDS)
        if not has_info:
            return RouteChoice(exact[0], "번호 일치 1개(지역·기점·종점 필드가 없어 번호로만 고름)")
    return RouteChoice(
        None,
        f"번호 일치 {len(exact)}개, 지역·기점·종점 조건 일치 {len(strict)}개 → 하나로 고르지 못함",
    )


def _route_brief(item: dict[str, Any]) -> dict[str, str]:
    return {k: _get(item, k) for k in _ROUTE_FIELDS if _get(item, k)}


# ---------------------------------------------------------------------------
# 정류장 목록 해석
# ---------------------------------------------------------------------------
def _station_brief(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "stationId": _get(item, "stationId"),
        "stationSeq": _seq(item),
        "stationName": _get(item, "stationName"),
    }


def analyze_stations(
    stations: list[dict[str, Any]], target: CollectTarget, route: TargetRoute, out: Printer
) -> dict[str, Any]:
    """근거를 출력하고 승차·하차 정류장을 고른다.

    승차는 덕현초교 후보 중 뒤에 하차 정류장이 있는 첫 곳, 하차는 그 뒤의 첫 하차 정류장이다.
    """
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
    chosen_board: dict[str, Any] | None = None
    chosen_alight: dict[str, Any] | None = None
    for index, s in boards:
        seq = _seq(s)
        prev_name = _get(ordered[index - 1], "stationName") if index > 0 else "(기점)"
        next_name = (
            _get(ordered[index + 1], "stationName") if index + 1 < len(ordered) else "(종점)"
        )
        later_alights = [a for a in alights if _seq(a) > seq]
        toward = (
            f"뒤에 하차 정류장 있음(순번 {_seq(later_alights[0])}) → {target.direction_label} 후보"
            if later_alights
            else "뒤에 하차 정류장 없음 → 반대 방향일 가능성"
        )
        out(
            f"    - stationId={_get(s, 'stationId')} stationSeq={seq} "
            f"mobileNo={_get(s, 'mobileNo')} name={_get(s, 'stationName')}"
        )
        out(f"      앞={prev_name} / 뒤={next_name}")
        out(f"      {_describe_direction(seq, turn_seq)}; {toward}")
        if chosen_board is None and later_alights:
            chosen_board = {
                **_station_brief(s),
                "prev_station_name": prev_name,
                "next_station_name": next_name,
            }
            chosen_alight = _station_brief(later_alights[0])

    return {
        "turn_seq": turn_seq,
        "board_candidates": len(boards),
        "alight_candidates": len(alights),
        "board": chosen_board,
        "alight": chosen_alight,
        "resolved": chosen_board is not None and chosen_alight is not None,
    }


# ---------------------------------------------------------------------------
# 노선 API 호출(하루 상한·원본 기록)
# ---------------------------------------------------------------------------
class _RouteApi:
    """노선 API 호출마다 상태 파일 카운터를 세고, 원본 기록을 기준정보 폴더에 남긴다."""

    def __init__(
        self,
        client: GbisClient,
        status: StatusStore,
        ref_dir: Path,
        clock: Clock,
        out: Printer,
    ) -> None:
        self._client = client
        self._status = status
        self._ref_dir = ref_dir
        self._clock = clock
        self._out = out
        self._sequence = 0

    def call(self, endpoint: GbisEndpoint, params: dict[str, str]) -> CallResult | None:
        """상한에 닿았으면 호출하지 않고 None."""
        service = endpoint.service
        at = to_kst(self._clock.now())
        if not self._status.can_call(service):
            self._status.mark_capped(service, at)
            self._status.save(at)
            self._out(
                f"  [{endpoint.api}] 노선 API 오늘 호출 상한"
                f"({self._status.limit_for(service)}회)에 닿아 호출하지 않는다"
            )
            return None
        # 호출 전에 카운트를 저장한다.
        # 수집 생존 표시(last_success_at 등)는 건드리지 않는다.
        self._status.begin_call(service, at, touch_liveness=False)
        self._status.save(at)
        result = self._client.call(endpoint, params)
        self._status.end_call(
            service, ok=result.ok, error=result.error, at=at, touch_liveness=False
        )
        self._status.save(at)
        self._save_record(result, at)
        self._out(_call_line(result))
        return result

    def _save_record(self, result: CallResult, at: datetime) -> None:
        self._sequence += 1
        target = "_".join(f"{k}-{v}" for k, v in result.params.items() if k != "format")
        safe_target = "".join(
            c if c.isascii() and (c.isalnum() or c in "-_") else "_" for c in target
        )
        prefix = f"{at:%H%M%S}_{self._sequence:02d}"
        name = f"{prefix}_{result.api}_{safe_target}{REFERENCE_RECORD_SUFFIX}"
        record = build_record(collected_at=at, result=result, mode=MODE_DISCOVER)
        write_json_atomic(self._ref_dir / name, record, self._client.redactor)


# ---------------------------------------------------------------------------
def discover(
    client: GbisClient,
    status: StatusStore,
    data_dir: Path,
    clock: Clock,
    printer: Printer = print,
    target: CollectTarget = COLLECT_TARGET,
) -> int:
    """노선별 routeId 와 승차·하차 정류장을 찾아 근거를 출력한다.

    결과는 기준정보 파일(원본 기록, targets.json)로 저장한다.

    종료 코드: 0 모두 찾음, 1 호출 실패 있음, 2 후보를 못 골랐거나 하루 상한으로 멈춤.
    노선 API 호출은 노선마다 최대 2회이고, 상태 파일의 하루 상한(4회)을 넘지 않는다.
    """

    # 응답 필드를 그대로 찍으므로, 키가 되돌아오는 드문 경우에 대비해 모든 줄을 가린다.
    def out(line: str) -> None:
        printer(client.redactor.redact(line))

    service = GBIS_ROUTE_LIST.service
    needed = CALLS_PER_ROUTE * len(target.routes)
    remaining = status.remaining(service)
    if remaining < needed:
        out(
            f"노선 API 오늘 남은 호출 {remaining}회 < 필요 {needed}회. 호출하지 않는다."
            " 하루 상한 때문에 discover 는 하루 1번만 실행한다(KST 0시에 다시 센다)."
        )
        return EXIT_NEEDS_DECISION

    now = to_kst(clock.now())
    ref_dir = reference_dir(data_dir, now.date())
    api = _RouteApi(client, status, ref_dir, clock, out)
    summary: dict[str, Any] = {
        "generated_at": now.isoformat(timespec="seconds"),
        "board_station_name": target.board_station_name,
        "direction_label": target.direction_label,
        "routes": [],
        "same_board_station_id": None,
    }
    has_failure = False
    needs_decision = False

    for route in target.routes:
        out(f"=== 노선 {route.route_name} (하차: {route.alight_station_name}) ===")
        entry: dict[str, Any] = {"route_name": route.route_name, "resolved": False}
        summary["routes"].append(entry)

        listed = api.call(GBIS_ROUTE_LIST, {"keyword": route.route_name})
        if listed is None:
            needs_decision = True
            entry["reason"] = "노선 API 하루 상한"
            break
        if not listed.ok:
            has_failure = True
            entry["reason"] = "노선 검색 실패"
            continue

        choice = choose_route(listed.items, route, target)
        out(f"  노선 후보 {len(listed.items)}개: {choice.reason}")
        for item in listed.items:
            mark = "*" if item is choice.chosen else " "
            out(f"   {mark} {_fields(item, _ROUTE_FIELDS)}")
        if choice.chosen is None:
            out("  정류장 목록을 부르지 않고 멈춘다. 위 후보를 보고 노선을 정한다.")
            entry["reason"] = choice.reason
            entry["candidates"] = [_route_brief(item) for item in listed.items]
            needs_decision = True
            break

        route_id = _get(choice.chosen, "routeId")
        entry.update(
            route_id=route_id, route_choice=choice.reason, route=_route_brief(choice.chosen)
        )
        stations = api.call(GBIS_ROUTE_STATIONS, {"routeId": route_id})
        if stations is None:
            needs_decision = True
            entry["reason"] = "노선 API 하루 상한"
            break
        if not stations.ok:
            has_failure = True
            entry["reason"] = "정류장 목록 실패"
            continue
        entry.update(analyze_stations(stations.items, target, route, out))
        if not entry["resolved"]:
            needs_decision = True
            entry["reason"] = "덕현초교 뒤에 하차 정류장이 있는 후보 없음"

    board_ids = {
        e["board"]["stationId"] for e in summary["routes"] if e.get("resolved") and e.get("board")
    }
    if all(e.get("resolved") for e in summary["routes"]):
        summary["same_board_station_id"] = len(board_ids) == 1
    targets_path = ref_dir / TARGETS_FILENAME
    write_json_atomic(targets_path, summary, client.redactor)

    out("")
    out(f"기준정보 저장: {ref_dir} (노선 API 원본 기록과 {TARGETS_FILENAME})")
    out(f"노선 API 오늘 호출 {status.calls(service)}/{status.limit_for(service)}회")
    out("다음: 근거와 targets.json 을 확인한 뒤 backend/app/core/settings.py 의")
    out("      COLLECT_TARGET.routes[].route_id 와 board_station_id 를 채운다.")
    if has_failure:
        return EXIT_CALL_FAILED
    return EXIT_NEEDS_DECISION if needs_decision else EXIT_OK
