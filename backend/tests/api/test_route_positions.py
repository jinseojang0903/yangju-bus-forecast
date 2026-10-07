"""GET /routes/{routeId}/positions(계약 4.8절). 실제 GBIS 를 부르지 않고 합성 픽스처만 쓴다."""

import json
import logging
import shutil
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.core.settings import API_RATE_LIMITS, KST
from app.repositories import route_positions as route_positions_repo
from app.repositories.route_positions import (
    ReverseLineReader,
    RoutePositionRepository,
    iter_lines_reversed,
)
from app.services.route_positions import stops_to_target
from tests.api.helpers import API, FIXED_NOW, STATION, AppFactory, assert_error

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "route_positions"
RAW_POLL_FIXTURE = FIXTURE_DIR / "raw_poll_synthetic.jsonl"
REFERENCE_FIXTURE = FIXTURE_DIR / "093514_02_getBusRouteStationListv2_routeId-235000092.record.json"
G1300 = "235000092"
ROUTE_1306 = "235000123"
URL = f"{API}/routes/{G1300}/positions"


def place_reference(data_dir: Path, day: str = "2026-10-06") -> None:
    target = data_dir / "reference" / day
    target.mkdir(parents=True, exist_ok=True)
    shutil.copy(REFERENCE_FIXTURE, target / REFERENCE_FIXTURE.name)


def raw_poll_file(data_dir: Path, day: str) -> Path:
    path = data_dir / day / "raw_poll.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def place_raw_poll(data_dir: Path, day: str = "2026-10-07") -> None:
    shutil.copy(RAW_POLL_FIXTURE, raw_poll_file(data_dir, day))


def location_line(
    collected_at: str,
    route_id: str,
    bus_location_list: Any,
    *,
    mode: str = "run",
    result_code: int = 0,
) -> dict[str, Any]:
    msg_body = None if bus_location_list is None else {"busLocationList": bus_location_list}
    return {
        "collected_at": collected_at,
        "api": "getBusLocationListv2",
        "params": {"routeId": route_id, "format": "json"},
        "http_status": 200,
        "ok": True,
        "result_code": str(result_code),
        "mode": mode,
        "interval_sec": 10,
        "body": {"response": {"msgHeader": {"resultCode": result_code}, "msgBody": msg_body}},
    }


def vehicle(veh_id: int, seq: int, state_cd: int = 1, seats: int = 5) -> dict[str, Any]:
    return {
        "plateNo": f"경기70바{veh_id % 10000:04d}",
        "remainSeatCnt": seats,
        "routeId": int(G1300),
        "stateCd": state_cd,
        "stationId": 100000000 + seq,
        "stationSeq": seq,
        "vehId": veh_id,
    }


def write_lines(path: Path, lines: list[dict[str, Any]], mode: str = "w") -> None:
    with path.open(mode, encoding="utf-8", newline="\n") as fp:
        for line in lines:
            fp.write(json.dumps(line, ensure_ascii=False, separators=(",", ":")) + "\n")


def get_body(app_factory: AppFactory, now: datetime = FIXED_NOW, **overrides: Any) -> Any:
    response = TestClient(app_factory(now=now, **overrides)).get(URL)
    assert response.status_code == 200, response.text
    return response.json()


def at(text: str) -> datetime:
    return datetime.fromisoformat(text)


# ---------------------------------------------------------------------------
# 정상 응답
# ---------------------------------------------------------------------------
def test_positions_ok(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir)

    body = get_body(app_factory)

    assert set(body) == {
        "routeId",
        "routeName",
        "targetStationId",
        "computedAt",
        "dataUpdatedAt",
        "stale",
        "inCollectionWindow",
        "nextRefreshAt",
        "stations",
        "vehicles",
    }
    assert body["routeId"] == G1300
    assert body["routeName"] == "G1300"
    assert body["targetStationId"] == STATION
    assert at(body["computedAt"]) == FIXED_NOW
    assert body["computedAt"].endswith("+09:00")
    assert body["inCollectionWindow"] is True
    assert body["stale"] is False
    # G1300 주기 10초: 07:31:20 다음 경계 07:31:30 + 3초
    assert at(body["nextRefreshAt"]) == datetime(2026, 10, 7, 7, 31, 33, tzinfo=KST)


def test_stations_sorted_with_target_and_outbound(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir)

    stations = get_body(app_factory)["stations"]

    assert [s["stationSeq"] for s in stations] == [1, 2, 3, 4, 5, 6]
    assert [s["isOutbound"] for s in stations] == [True, True, True, True, False, False]
    assert [s["isTarget"] for s in stations] == [False, False, True, False, False, False]
    # 회차 전후 같은 정류장(순번 1·6)은 둘 다 준다
    assert stations[0]["stationId"] == stations[5]["stationId"] == "100000001"
    assert stations[2] == {
        "stationSeq": 3,
        "stationId": STATION,
        "name": "덕현초교.덕고개",
        "lat": 37.79825,
        "lng": 127.0807333,
        "isOutbound": True,
        "isTarget": True,
    }


def test_vehicles_from_latest_ok_line(app_factory: AppFactory, data_dir: Path) -> None:
    # 픽스처: 07:31:00 G1300 줄(차량 900000001) → 07:31:10 G1300 줄(4대) → 07:31:20 실패 줄
    # → 쓰는 중인 깨진 줄. 마지막 '성공' 줄인 07:31:10 을 써야 한다.
    place_reference(data_dir)
    place_raw_poll(data_dir)

    body = get_body(app_factory)

    assert at(body["dataUpdatedAt"]) == at("2026-10-07T07:31:10.003+09:00")
    assert body["vehicles"] == [
        {
            "vehicleId": "900000002",
            "plateNo": "경기70바0002",
            "stationSeq": 1,
            "stationId": "100000001",
            "state": "departed",
            "remainSeats": 10,
            "stopsToTarget": 2,
        },
        {
            "vehicleId": "900000005",
            "plateNo": "경기70바0005",
            "stationSeq": 2,
            "stationId": "100000002",
            "state": "unknown",  # stateCd 9
            "remainSeats": None,  # remainSeatCnt 없음
            "stopsToTarget": 1,
        },
        {
            "vehicleId": "900000003",
            "plateNo": "경기70바0003",
            "stationSeq": 3,
            "stationId": STATION,
            "state": "arrived",
            "remainSeats": None,  # -1 은 정보 없음
            "stopsToTarget": 0,
        },
        {
            "vehicleId": "900000004",
            "plateNo": "경기70바0004",
            "stationSeq": 5,
            "stationId": "235000409",
            "state": "passing",
            "remainSeats": 0,  # 0석은 null 이 아니다
            "stopsToTarget": None,  # 회차 뒤
        },
    ]


def test_works_without_fake_data(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir)

    body = get_body(app_factory, fake_data=False)

    assert len(body["vehicles"]) == 4


def test_single_dict_location_list(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [location_line("2026-10-07T07:31:10+09:00", G1300, vehicle(900000010, 2))],
    )

    vehicles = get_body(app_factory)["vehicles"]

    assert [v["vehicleId"] for v in vehicles] == ["900000010"]


def test_no_result_line_means_zero_vehicles(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [
            location_line("2026-10-07T07:31:00+09:00", G1300, [vehicle(900000010, 2)]),
            location_line("2026-10-07T07:31:10+09:00", G1300, None, result_code=4),
        ],
    )

    body = get_body(app_factory)

    assert body["vehicles"] == []
    assert at(body["dataUpdatedAt"]) == at("2026-10-07T07:31:10+09:00")


def test_trial_line_is_used(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [
            location_line("2026-10-07T07:31:00+09:00", G1300, [vehicle(900000010, 2)]),
            location_line(
                "2026-10-07T07:31:10+09:00", G1300, [vehicle(900000011, 3)], mode="trial"
            ),
        ],
    )

    vehicles = get_body(app_factory)["vehicles"]

    assert [v["vehicleId"] for v in vehicles] == ["900000011"]


# ---------------------------------------------------------------------------
# 전날 폴더로 거슬러 가기, 기록 없음
# ---------------------------------------------------------------------------
def test_falls_back_to_previous_day(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    # 오늘 파일에는 1306 줄만 있다 → 어제 파일의 G1300 마지막 줄을 쓴다.
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [location_line("2026-10-07T07:31:00+09:00", ROUTE_1306, [vehicle(900000020, 2)])],
    )
    write_lines(
        raw_poll_file(data_dir, "2026-10-06"),
        [
            location_line("2026-10-06T10:14:40+09:00", G1300, [vehicle(900000030, 2)]),
            location_line("2026-10-06T10:14:50+09:00", G1300, [vehicle(900000031, 4)]),
        ],
    )

    body = get_body(app_factory)

    assert at(body["dataUpdatedAt"]) == at("2026-10-06T10:14:50+09:00")
    assert [v["vehicleId"] for v in body["vehicles"]] == ["900000031"]
    assert body["stale"] is True  # 수집 시간 안인데 하루 전 기록


def test_skips_missing_day_folder(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir, day="2026-10-02")  # 금요일. 그 뒤 날짜 폴더는 없다

    body = get_body(app_factory)

    assert at(body["dataUpdatedAt"]) == at("2026-10-07T07:31:10.003+09:00")


def test_no_position_records(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)

    body = get_body(app_factory)

    assert body["vehicles"] == []
    assert body["dataUpdatedAt"] is None
    assert body["stale"] is True
    assert len(body["stations"]) == 6


# ---------------------------------------------------------------------------
# stale·수집 시간·다음 조회 시각
# ---------------------------------------------------------------------------
def test_stale_when_data_older_than_limit(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir)

    # 데이터 07:31:10.003 + 60초 = 07:32:10.003. 그 뒤면 오래됨.
    assert get_body(app_factory, now=at("2026-10-07T07:32:10+09:00"))["stale"] is False
    assert get_body(app_factory, now=at("2026-10-07T07:32:11+09:00"))["stale"] is True


def test_outside_collection_window(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir)

    body = get_body(app_factory, now=at("2026-10-07T11:00:00+09:00"))

    assert body["inCollectionWindow"] is False
    assert body["stale"] is False
    assert len(body["vehicles"]) == 4  # 마지막 기록 그대로
    assert at(body["nextRefreshAt"]) == at("2026-10-08T05:30:00+09:00")


def test_next_refresh_uses_route_interval(app_factory: AppFactory, data_dir: Path) -> None:
    # 1306 주기 30초. 기준정보를 1306 이름으로 복사해 쓴다.
    target = data_dir / "reference" / "2026-10-06"
    target.mkdir(parents=True)
    shutil.copy(
        REFERENCE_FIXTURE,
        target / "093515_04_getBusRouteStationListv2_routeId-235000123.record.json",
    )

    response = TestClient(app_factory()).get(f"{API}/routes/{ROUTE_1306}/positions")

    assert response.status_code == 200
    body = response.json()
    assert body["routeName"] == "1306"
    assert at(body["nextRefreshAt"]) == datetime(2026, 10, 7, 7, 31, 33, tzinfo=KST)
    # 07:31:20 다음 30초 경계는 07:31:30 이다(10초 주기와 같은 경계)
    later = TestClient(app_factory(now=FIXED_NOW + timedelta(seconds=15))).get(
        f"{API}/routes/{ROUTE_1306}/positions"
    )
    assert at(later.json()["nextRefreshAt"]) == datetime(2026, 10, 7, 7, 32, 3, tzinfo=KST)


# ---------------------------------------------------------------------------
# 캐시
# ---------------------------------------------------------------------------
def test_response_is_cached(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    place_raw_poll(data_dir)
    client = TestClient(app_factory())

    first = client.get(URL).json()
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [location_line("2026-10-07T07:31:19+09:00", G1300, [vehicle(900000099, 2)])],
        mode="a",
    )
    second = client.get(URL).json()

    assert second == first


# ---------------------------------------------------------------------------
# 에러
# ---------------------------------------------------------------------------
def test_unsupported_route_is_404(client: TestClient, data_dir: Path) -> None:
    place_reference(data_dir)

    # 수집은 하지만 지도 지원 노선이 아닌 1100
    assert_error(client.get(f"{API}/routes/235000085/positions"), 404, "NOT_FOUND")


def test_missing_reference_is_404(client: TestClient, data_dir: Path) -> None:
    place_reference(data_dir)  # G1300 기준정보만 있다
    place_raw_poll(data_dir)

    assert_error(client.get(f"{API}/routes/{ROUTE_1306}/positions"), 404, "NOT_FOUND")


def test_broken_reference_is_404(client: TestClient, data_dir: Path) -> None:
    target = data_dir / "reference" / "2026-10-06"
    target.mkdir(parents=True)
    (target / REFERENCE_FIXTURE.name).write_text("{not json", encoding="utf-8")

    assert_error(client.get(URL), 404, "NOT_FOUND")


def test_uses_latest_usable_reference_day(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir, day="2026-10-05")
    newer = data_dir / "reference" / "2026-10-06"
    newer.mkdir(parents=True)
    (newer / REFERENCE_FIXTURE.name).write_text("{not json", encoding="utf-8")

    assert len(get_body(app_factory)["stations"]) == 6


@pytest.mark.parametrize("route_id", ["abc", "12a", "1" * 21, "-1", "1.5"])
def test_bad_route_id_is_400(client: TestClient, route_id: str) -> None:
    body = assert_error(client.get(f"{API}/routes/{route_id}/positions"), 400, "VALIDATION_FAILED")

    assert body["error"]["details"] == [{"field": "routeId", "reason": "숫자 1–20자리여야 합니다"}]


def test_shares_default_get_rate_limit(app_factory: AppFactory, data_dir: Path) -> None:
    place_reference(data_dir)
    client = TestClient(app_factory())
    limit = API_RATE_LIMITS.default_get[0].limit
    for _ in range(limit):
        client.get(f"{API}/stations")

    response = client.get(URL)

    assert_error(response, 429, "RATE_LIMITED")
    assert response.headers["Retry-After"]


# ---------------------------------------------------------------------------
# 파일 끝에서부터 읽기
# ---------------------------------------------------------------------------
def test_iter_lines_reversed_across_chunks(tmp_path: Path) -> None:
    path = tmp_path / "lines.jsonl"
    lines = [f"줄{i}-" + "x" * i for i in range(30)]
    path.write_bytes(("\n".join(lines) + "\n\n").encode("utf-8"))

    for chunk_size in (1, 3, 7, 64, 4096):
        got = [raw.decode("utf-8") for raw in iter_lines_reversed(path, chunk_size)]
        assert got == list(reversed(lines))


def test_reader_stops_at_limit_without_partial_line(tmp_path: Path) -> None:
    path = tmp_path / "lines.jsonl"
    path.write_bytes(b"aaaa\nbbbb\ncccc\n")  # 15바이트

    reader = ReverseLineReader(path, chunk_size=4, max_bytes=8)
    got = list(reader)

    # 끝 8바이트 = "bb\ncccc\n" → 온전한 줄은 cccc 뿐. 잘린 "bb" 는 돌려주지 않는다.
    assert got == [b"cccc"]
    assert reader.hit_limit is True

    whole = ReverseLineReader(path, chunk_size=4, max_bytes=100)
    assert list(whole) == [b"cccc", b"bbbb", b"aaaa"]
    assert whole.hit_limit is False


# ---------------------------------------------------------------------------
# 쓸 수 없는 위치 줄 건너뛰기(ok=true 인데 본문이 이상한 줄)
# ---------------------------------------------------------------------------
def _with_body(line: dict[str, Any], body: Any) -> dict[str, Any]:
    return {**line, "body": body}


_GOOD_TIME = "2026-10-07T07:31:00+09:00"
_BAD_TIME = "2026-10-07T07:31:10+09:00"


@pytest.mark.parametrize(
    "bad_body",
    [
        "<html>not json</html>",  # body 가 객체가 아님
        {"response": "oops"},  # response 가 객체가 아님
        {"response": {"msgHeader": {"resultCode": 0}}},  # 성공인데 msgBody 없음
        {"response": {"msgHeader": {"resultCode": 0}, "msgBody": {"other": []}}},  # 목록 없음
        {"response": {"msgHeader": {"resultCode": 0}, "msgBody": {"busLocationList": "x"}}},
        {"response": {"msgHeader": {"resultCode": 99}, "msgBody": None}},  # 오류 코드
        {"response": {"msgHeader": {}, "msgBody": None}},  # 코드 없음
        {"response": {"header": {"resultCode": "00"}}},  # 다른 형식 header
    ],
)
def test_unusable_ok_line_is_skipped(
    app_factory: AppFactory, data_dir: Path, bad_body: Any
) -> None:
    place_reference(data_dir)
    good = location_line(_GOOD_TIME, G1300, [vehicle(900000040, 2)])
    bad = _with_body(location_line(_BAD_TIME, G1300, []), bad_body)
    write_lines(raw_poll_file(data_dir, "2026-10-07"), [good, bad])

    body = get_body(app_factory)

    assert at(body["dataUpdatedAt"]) == at(_GOOD_TIME)
    assert [v["vehicleId"] for v in body["vehicles"]] == ["900000040"]


@pytest.mark.parametrize(
    "zero_body",
    [
        {"response": {"msgHeader": {"resultCode": 4}, "msgBody": None}},  # 결과 없음
        {"response": {"msgHeader": {"resultCode": "04"}, "msgBody": ""}},  # 결과 없음(문자열)
        {"response": {"msgHeader": {"resultCode": 0}, "msgBody": {"busLocationList": []}}},
        {"response": {"comMsgHeader": ""}},  # 빈 응답(수집기도 결과 없음으로 본다)
    ],
)
def test_no_result_variants_mean_zero_vehicles(
    app_factory: AppFactory, data_dir: Path, zero_body: Any
) -> None:
    place_reference(data_dir)
    good = location_line(_GOOD_TIME, G1300, [vehicle(900000040, 2)])
    zero = _with_body(location_line(_BAD_TIME, G1300, []), zero_body)
    write_lines(raw_poll_file(data_dir, "2026-10-07"), [good, zero])

    body = get_body(app_factory)

    assert at(body["dataUpdatedAt"]) == at(_BAD_TIME)
    assert body["vehicles"] == []


# ---------------------------------------------------------------------------
# 파일당 읽기 상한·읽기 실패
# ---------------------------------------------------------------------------
def test_read_limit_moves_to_previous_day(data_dir: Path, caplog: pytest.LogCaptureFixture) -> None:
    today = raw_poll_file(data_dir, "2026-10-07")
    # 오늘: G1300 줄 뒤에 1306 줄이 상한보다 많이 쌓였다 → 상한 안에서 G1300 을 못 찾는다.
    filler = [
        location_line(f"2026-10-07T07:{i // 6:02d}:{i % 6 * 10:02d}+09:00", ROUTE_1306, [])
        for i in range(60)
    ]
    write_lines(today, [location_line(_GOOD_TIME, G1300, [vehicle(900000050, 2)]), *filler])
    write_lines(
        raw_poll_file(data_dir, "2026-10-06"),
        [location_line("2026-10-06T10:14:50+09:00", G1300, [vehicle(900000051, 3)])],
    )
    repository = RoutePositionRepository(data_dir, max_bytes_per_file=4096)
    assert today.stat().st_size > 4096
    caplog.set_level(logging.WARNING, logger="app.repositories.route_positions")

    found = repository.find_latest_positions(
        G1300, date(2026, 10, 7), lookback_days=7, include_trial=True
    )

    assert found is not None
    assert [r.veh_id for r in found.records] == ["900000051"]
    messages = [r.getMessage() for r in caplog.records]
    assert messages == ["route_positions_read_limit route_id=235000092 day=2026-10-07"]


def test_read_limit_default_finds_line_within_limit(data_dir: Path) -> None:
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [location_line(_GOOD_TIME, G1300, [vehicle(900000050, 2)])],
    )

    found = RoutePositionRepository(data_dir).find_latest_positions(
        G1300, date(2026, 10, 7), lookback_days=7, include_trial=True
    )

    assert found is not None and [r.veh_id for r in found.records] == ["900000050"]


def test_unreadable_day_is_skipped(
    data_dir: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    write_lines(
        raw_poll_file(data_dir, "2026-10-07"),
        [location_line(_GOOD_TIME, G1300, [vehicle(900000060, 2)])],
    )
    write_lines(
        raw_poll_file(data_dir, "2026-10-06"),
        [location_line("2026-10-06T10:14:50+09:00", G1300, [vehicle(900000061, 3)])],
    )
    original = route_positions_repo.find_latest_positions_in_file

    def flaky(path: Path, *args: Any, **kwargs: Any) -> Any:
        if path.parent.name == "2026-10-07":
            raise PermissionError(13, "denied", str(path))
        return original(path, *args, **kwargs)

    monkeypatch.setattr(route_positions_repo, "find_latest_positions_in_file", flaky)
    caplog.set_level(logging.WARNING, logger="app.repositories.route_positions")

    found = RoutePositionRepository(data_dir).find_latest_positions(
        G1300, date(2026, 10, 7), lookback_days=7, include_trial=True
    )

    assert found is not None
    assert [r.veh_id for r in found.records] == ["900000061"]
    messages = [r.getMessage() for r in caplog.records]
    assert messages == [
        "route_positions_unreadable route_id=235000092 day=2026-10-07 error=PermissionError"
    ]


@pytest.mark.parametrize(
    ("vehicle_seq", "state", "expected"),
    [
        (10, "passing", 3),
        (13, "arrived", 0),
        (13, "passing", 0),
        (13, "departed", None),  # 내 정류장을 이미 떠난 차
        (14, "arrived", None),
    ],
)
def test_stops_to_target_excludes_departed_from_target(
    vehicle_seq: int, state: str, expected: int | None
) -> None:
    assert stops_to_target(vehicle_seq, 13, state) == expected
