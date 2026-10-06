import json

import httpx

from app.collector.discover import discover
from app.collector.redact import Redactor
from tests.collector.helpers import TEST_TARGET, make_client, make_settings


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


def handler(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    params = request.url.params
    if path.endswith("getBusRouteListv2"):
        keyword = params["keyword"]
        return _ok(
            "busRouteList",
            [
                {"routeId": f"9{keyword[-4:]}", "routeName": keyword, "regionName": "양주"},
                {"routeId": "99999", "routeName": f"{keyword}-1", "regionName": "양주"},
            ],
        )
    if path.endswith("getBusRouteInfoItemv2"):
        return _ok(
            "busRouteInfoItem",
            {"routeId": params["routeId"], "startStationName": "기점", "endStationName": "종점"},
        )
    if path.endswith("getBusRouteStationListv2"):
        alight = "잠실광역환승센터" if params["routeId"] == "91300" else "잠실역"
        return _ok("busRouteStationList", _stations(params["routeId"], alight))
    return httpx.Response(404, text="not found")


def test_discover_prints_candidates_with_evidence() -> None:
    settings = make_settings()
    client = make_client(handler, settings, Redactor(settings.secret_values()))
    lines: list[str] = []
    exit_code = discover(client, lines.append, TEST_TARGET)
    text = "\n".join(lines)

    assert exit_code == 0
    assert "routeId=91300" in text and "routeId=91306" in text
    assert "번호 정확히 일치 1개" in text
    # 덕현초교 두 곳: 회차 전(잠실행 후보)과 회차 후(반대 방향)
    assert "stationId=9130002 stationSeq=2" in text
    assert "뒤에 하차 정류장 있음(순번 4)" in text
    assert "뒤에 하차 정류장 없음" in text
    assert "앞=기점 / 뒤=고읍중" in text
    assert "startStationName=기점" in text
