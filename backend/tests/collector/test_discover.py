"""discover: 노선 API 하루 4회 이하, 추가 호출 없이 후보 고르기, 기준정보 저장."""

import json
from collections.abc import Callable
from pathlib import Path

import httpx

from app.collector.discover import (
    EXIT_NEEDS_DECISION,
    EXIT_OK,
    _RouteApi,
    choose_route,
    discover,
)
from app.collector.gbis import GbisClient
from app.collector.redact import Redactor
from app.collector.status import StatusStore, status_path
from app.collector.storage import reference_dir
from app.core.settings import (
    CALL_LIMITS,
    GBIS_ROUTE_LIST,
    REFERENCE_RECORD_SUFFIX,
    TARGETS_FILENAME,
)
from tests.collector.helpers import TEST_TARGET, FakeClock, kst, make_client, make_settings

ROUTE_SERVICE = "busrouteservice"
DAY = kst(2026, 10, 7, 4, 30)


def _ok(list_key: str, items: object) -> httpx.Response:
    body = {
        "response": {
            "msgHeader": {"resultCode": 0, "resultMessage": "정상적으로 처리되었습니다."},
            "msgBody": {list_key: items},
        }
    }
    return httpx.Response(200, text=json.dumps(body, ensure_ascii=False))


def _stations(route_id: str, alight: str) -> list[dict]:
    names = ["기점", "덕현초교", "고읍중", alight, "덕현초교", "종점"]
    return [
        {
            "stationId": f"{route_id}{seq:02d}",
            "stationName": name,
            "stationSeq": seq,
            "turnYn": "Y" if name == alight else "N",
            "mobileNo": f"{seq:05d}",
        }
        for seq, name in enumerate(names, start=1)
    ]


class RouteHandler:
    """노선 검색은 같은 번호 후보 여럿(양주·서울)과 비슷한 번호를 돌려준다."""

    def __init__(self, route_items: Callable[[str], list[dict]] | None = None) -> None:
        self.requests: list[httpx.Request] = []
        self._route_items = route_items or self.default_route_items

    @staticmethod
    def default_route_items(keyword: str) -> list[dict]:
        return [
            {"routeId": "80000", "routeName": keyword, "regionName": "서울"},
            {"routeId": f"9{keyword[-4:]}", "routeName": keyword, "regionName": "양주"},
            {"routeId": "99999", "routeName": f"{keyword}-1", "regionName": "양주"},
        ]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        params = request.url.params
        if path.endswith("getBusRouteListv2"):
            return _ok("busRouteList", self._route_items(params["keyword"]))
        if path.endswith("getBusRouteStationListv2"):
            alight = "잠실광역환승센터" if params["routeId"] == "91300" else "잠실역"
            return _ok("busRouteStationList", _stations(params["routeId"], alight))
        return httpx.Response(404, text="not found")

    def paths(self) -> list[str]:
        return [r.url.path.rsplit("/", 1)[-1] for r in self.requests]


def _env(data_dir: Path, handler: RouteHandler) -> tuple[GbisClient, StatusStore, FakeClock]:
    settings = make_settings()
    redactor = Redactor(settings.secret_values())
    clock = FakeClock(DAY)
    status = StatusStore(
        status_path(data_dir), redactor, CALL_LIMITS, today=DAY.date(), mode="discover"
    )
    return make_client(handler, settings, redactor), status, clock


def _targets(data_dir: Path) -> dict:
    path = reference_dir(data_dir, DAY.date()) / TARGETS_FILENAME
    return json.loads(path.read_text(encoding="utf-8"))


def test_route_limit_is_four() -> None:
    assert CALL_LIMITS.route_daily_limit == 4
    assert CALL_LIMITS.safe_limit_for(ROUTE_SERVICE) == 4
    assert CALL_LIMITS.safe_limit_for("buslocationservice") == 980


def test_discover_uses_four_calls_and_saves_reference(data_dir: Path) -> None:
    handler = RouteHandler()
    client, status, clock = _env(data_dir, handler)
    lines: list[str] = []
    exit_code = discover(client, status, data_dir, clock, lines.append, TEST_TARGET)
    text = "\n".join(lines)

    assert exit_code == EXIT_OK
    assert handler.paths() == [
        "getBusRouteListv2",
        "getBusRouteStationListv2",
        "getBusRouteListv2",
        "getBusRouteStationListv2",
    ]
    assert "getBusRouteInfoItemv2" not in handler.paths()
    # 근거 출력: 덕현초교 두 곳(회차 전·후), 앞·뒤 정류장
    assert "stationId=9130002 stationSeq=2" in text
    assert "뒤에 하차 정류장 있음(순번 4)" in text
    assert "뒤에 하차 정류장 없음" in text
    assert "앞=기점 / 뒤=고읍중" in text

    targets = _targets(data_dir)
    g1300, r1306 = targets["routes"]
    assert g1300["route_id"] == "91300" and r1306["route_id"] == "91306"
    assert g1300["resolved"] is True
    assert g1300["board"]["stationId"] == "9130002"
    assert g1300["board"]["stationSeq"] == 2
    assert g1300["board"]["next_station_name"] == "고읍중"
    assert g1300["alight"] == {
        "stationId": "9130004",
        "stationSeq": 4,
        "stationName": "잠실광역환승센터",
    }
    assert r1306["alight"]["stationName"] == "잠실역"
    assert targets["same_board_station_id"] is False

    records = list(reference_dir(data_dir, DAY.date()).glob(f"*{REFERENCE_RECORD_SUFFIX}"))
    assert len(records) == 4
    saved = json.loads((data_dir / "status.json").read_text(encoding="utf-8"))
    assert saved["apis"][ROUTE_SERVICE]["calls"] == 4
    assert saved["last_success_at"] is None  # 수집 생존 표시는 건드리지 않는다


def test_second_discover_same_day_makes_no_calls(data_dir: Path) -> None:
    handler = RouteHandler()
    client, status, clock = _env(data_dir, handler)
    discover(client, status, data_dir, clock, lambda _: None, TEST_TARGET)
    assert len(handler.requests) == 4

    # 재시작(상태 파일에서 다시 읽음) 뒤 다시 실행해도 호출하지 않는다.
    client2, status2, clock2 = _env(data_dir, handler)
    lines: list[str] = []
    assert discover(client2, status2, data_dir, clock2, lines.append, TEST_TARGET) == (
        EXIT_NEEDS_DECISION
    )
    assert len(handler.requests) == 4
    assert any("남은 호출 0회" in line for line in lines)


def test_partially_used_budget_refuses_before_any_call(data_dir: Path) -> None:
    handler = RouteHandler()
    client, status, clock = _env(data_dir, handler)
    status.begin_call(ROUTE_SERVICE, DAY)  # 오늘 이미 1회 썼다
    assert discover(client, status, data_dir, clock, lambda _: None, TEST_TARGET) == (
        EXIT_NEEDS_DECISION
    )
    assert handler.requests == []


def test_route_api_guard_never_exceeds_limit(data_dir: Path) -> None:
    handler = RouteHandler()
    client, status, clock = _env(data_dir, handler)
    for _ in range(4):
        status.begin_call(ROUTE_SERVICE, DAY)
    lines: list[str] = []
    api = _RouteApi(client, status, data_dir / "ref", clock, lines.append)
    assert api.call(GBIS_ROUTE_LIST, {"keyword": "G1300"}) is None
    assert handler.requests == []
    assert status.calls(ROUTE_SERVICE) == 4
    assert any("상한" in line for line in lines)


def test_ambiguous_candidates_stop_without_station_list(data_dir: Path) -> None:
    def two_in_yangju(keyword: str) -> list[dict]:
        return [
            {"routeId": "91300", "routeName": keyword, "regionName": "양주"},
            {"routeId": "91301", "routeName": keyword, "regionName": "양주"},
        ]

    handler = RouteHandler(two_in_yangju)
    client, status, clock = _env(data_dir, handler)
    lines: list[str] = []
    exit_code = discover(client, status, data_dir, clock, lines.append, TEST_TARGET)

    assert exit_code == EXIT_NEEDS_DECISION
    assert handler.paths() == ["getBusRouteListv2"]
    assert any("routeId=91301" in line for line in lines)
    entry = _targets(data_dir)["routes"][0]
    assert entry["resolved"] is False
    assert [c["routeId"] for c in entry["candidates"]] == ["91300", "91301"]


def test_choose_route_rules() -> None:
    route = TEST_TARGET.routes[0]  # G1300
    # 같은 번호 여럿 중 지역 '양주' 하나
    picked = choose_route(
        [
            {"routeId": "1", "routeName": "G1300", "regionName": "서울"},
            {"routeId": "2", "routeName": "G1300", "regionName": "양주"},
        ],
        route,
        TEST_TARGET,
    )
    assert picked.chosen is not None and picked.chosen["routeId"] == "2"
    # 지역 정보가 없으면 기점·종점의 '잠실'
    picked = choose_route(
        [
            {"routeId": "1", "routeName": "G1300", "endStationName": "강남역"},
            {"routeId": "2", "routeName": "G1300", "endStationName": "잠실광역환승센터"},
        ],
        route,
        TEST_TARGET,
    )
    assert picked.chosen is not None and picked.chosen["routeId"] == "2"
    # 번호 일치 하나뿐이고 확인할 필드가 아예 없으면 그것
    picked = choose_route([{"routeId": "3", "routeName": "G1300"}], route, TEST_TARGET)
    assert picked.chosen is not None and picked.chosen["routeId"] == "3"
    # 번호 일치 하나지만 지역이 다르면 고르지 않는다
    picked = choose_route(
        [{"routeId": "4", "routeName": "G1300", "regionName": "서울"}], route, TEST_TARGET
    )
    assert picked.chosen is None
    # 번호 일치가 없으면 고르지 않는다
    picked = choose_route(
        [{"routeId": "5", "routeName": "G1300-1", "regionName": "양주"}], route, TEST_TARGET
    )
    assert picked.chosen is None


def test_corrupt_status_recovers_route_calls_from_reference(data_dir: Path) -> None:
    handler = RouteHandler()
    client, status, clock = _env(data_dir, handler)
    discover(client, status, data_dir, clock, lambda _: None, TEST_TARGET)
    (data_dir / "status.json").write_text("{broken", encoding="utf-8")

    _, recovered, _ = _env(data_dir, handler)
    assert recovered.calls(ROUTE_SERVICE) == 4
    assert recovered.can_call(ROUTE_SERVICE) is False
