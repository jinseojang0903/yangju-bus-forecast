from fastapi.testclient import TestClient

from tests.api.helpers import API, STATION, assert_error


def test_list_stations(client: TestClient) -> None:
    response = client.get(f"{API}/stations")

    assert response.status_code == 200
    assert response.json() == {
        "items": [
            {
                "stationId": STATION,
                "name": "덕현초교.덕고개",
                "directionLabel": "잠실행",
                "mobileNo": "39624",
            }
        ]
    }


def test_station_routes(client: TestClient) -> None:
    response = client.get(f"{API}/stations/{STATION}/routes")

    assert response.status_code == 200
    body = response.json()
    assert body["stationId"] == STATION
    [destination] = body["destinations"]
    assert destination["destinationId"] == "jamsil"
    assert destination["name"] == "잠실"
    assert {(r["routeId"], r["routeName"]) for r in destination["routes"]} == {
        ("235000092", "G1300"),
        ("235000123", "1306"),
    }
    assert set(destination["routes"][0]) == {
        "routeId",
        "routeName",
        "alightStationId",
        "alightStationName",
    }


def test_station_routes_unknown_station_is_404(client: TestClient) -> None:
    assert_error(client.get(f"{API}/stations/123/routes"), 404, "NOT_FOUND")


def test_station_routes_bad_id_is_400(client: TestClient) -> None:
    body = assert_error(client.get(f"{API}/stations/abc/routes"), 400, "VALIDATION_FAILED")

    assert body["error"]["details"] == [
        {"field": "stationId", "reason": "숫자 1–20자리여야 합니다"}
    ]
