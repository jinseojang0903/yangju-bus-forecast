from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_repository
from app.core.settings import FORECAST_RULES
from app.forecast.risk import risk_level
from app.repositories.fake import FakeRepository
from app.schemas.snapshot import SNAPSHOT_SCENARIOS
from tests.api.helpers import (
    API,
    DESTINATION,
    STATION,
    AppFactory,
    assert_error,
    contract_example_snapshot,
)

SNAPSHOT = f"{API}/snapshot"


def get_snapshot(client: TestClient, **params: str) -> Any:
    query = {"station": STATION, "destination": DESTINATION, **params}
    return client.get(SNAPSHOT, params=query)


def selected(bus: dict[str, Any]) -> dict[str, Any] | None:
    lead = bus["selectedLeadTimeMin"]
    return next((f for f in bus["forecasts"] if f["leadTimeMin"] == lead), None)


def scenario(client: TestClient, name: str) -> dict[str, Any]:
    response = get_snapshot(client, scenario=name)
    assert response.status_code == 200, response.text
    return response.json()


# ---------------------------------------------------------------------------
# 9장 예시와 값 수준 일치
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "params",
    [
        {},
        {"scenario": "example"},
        {"deadline": "08:30"},
        {"deadline": "08:30", "scenario": "example"},
    ],
)
def test_default_and_example_match_contract_section_9(
    client: TestClient, params: dict[str, str]
) -> None:
    response = get_snapshot(client, **params)

    assert response.status_code == 200
    assert response.json() == contract_example_snapshot()


def test_published_is_example_without_preliminary(client: TestClient) -> None:
    published = scenario(client, "published")
    example = contract_example_snapshot()

    assert published["snapshotId"] != example["snapshotId"]
    for bus in published["buses"]:
        assert all(f["preliminary"] is False for f in bus["forecasts"])
    for bus in example["buses"]:
        for forecast in bus["forecasts"]:
            forecast["preliminary"] = False
    example["snapshotId"] = published["snapshotId"]
    assert published == example


# ---------------------------------------------------------------------------
# scenario 별 상태 재현(계약 12장)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    ("name", "expected_status"),
    [
        ("status_ok", "ok"),
        ("status_insufficient_cases", "insufficient_cases"),
        ("status_not_validated", "not_validated"),
        ("status_stale", "stale"),
        ("status_missing_input", "missing_input"),
        ("status_outside_hours", "outside_hours"),
    ],
)
def test_status_scenarios_set_first_bus_selected_status(
    client: TestClient, name: str, expected_status: str
) -> None:
    body = scenario(client, name)
    first = selected(body["buses"][0])

    assert first is not None
    assert first["status"] == expected_status
    if expected_status != "ok":
        assert first["noSeatProbability"] is None
        assert first["riskLevel"] is None


def test_status_not_yet_has_no_issued_forecast(client: TestClient) -> None:
    body = scenario(client, "status_not_yet")

    for bus in body["buses"]:
        assert bus["selectedLeadTimeMin"] is None
        assert {f["status"] for f in bus["forecasts"]} == {"not_yet"}
        assert all(f["issuedAt"] is None for f in bus["forecasts"])
    assert body["alternatives"]["status"] == "undecidable"


def test_status_ok_without_deadline_still_recommends(client: TestClient) -> None:
    body = scenario(client, "status_ok")

    assert body["deadline"] is None
    assert all(c["meetsDeadline"] is None for c in body["alternatives"]["candidates"])
    assert body["alternatives"]["status"] == "recommended"


def test_status_insufficient_cases_keeps_case_count(client: TestClient) -> None:
    first = selected(scenario(client, "status_insufficient_cases")["buses"][0])

    assert first is not None
    assert first["n"] is not None and first["n"] < FORECAST_RULES.min_cases_to_show


def test_status_stale_marks_snapshot_stale(client: TestClient) -> None:
    assert scenario(client, "status_stale")["stale"] is True


def test_status_missing_input_has_missing_headway(client: TestClient) -> None:
    first = selected(scenario(client, "status_missing_input")["buses"][0])

    assert first is not None
    assert first["inputs"]["headwayMin"] is None


def test_status_outside_hours_bus_is_outside_forecast_hours(client: TestClient) -> None:
    body = scenario(client, "status_outside_hours")

    assert body["service"]["state"] == "in_service"
    assert body["buses"][0]["inForecastHours"] is False
    assert body["buses"][1]["inForecastHours"] is True


@pytest.mark.parametrize(
    ("name", "expected_status"),
    [
        ("alt_recommended", "recommended"),
        ("alt_no_alternative", "no_alternative"),
        ("alt_arrival_unavailable", "arrival_unavailable"),
        ("alt_undecidable", "undecidable"),
    ],
)
def test_alternative_scenarios(client: TestClient, name: str, expected_status: str) -> None:
    alternatives = scenario(client, name)["alternatives"]

    assert alternatives["status"] == expected_status
    if expected_status == "recommended":
        assert alternatives["recommended"] is not None
    else:
        assert alternatives["recommended"] is None
        assert alternatives["switchSuggested"] is False


def test_alt_recommended_breaks_tie_by_earliest(client: TestClient) -> None:
    alternatives = scenario(client, "alt_recommended")["alternatives"]

    assert alternatives["recommended"]["routeId"] == "235000092"
    assert alternatives["recommended"]["reasonCode"] == "earliest_among_equal_risk"


def test_alt_arrival_unavailable_has_no_destination_times(client: TestClient) -> None:
    candidates = scenario(client, "alt_arrival_unavailable")["alternatives"]["candidates"]

    assert all(c["destinationArrivalAt"] is None for c in candidates)
    assert all(c["meetsDeadline"] is None for c in candidates)


def test_service_outside_collection(client: TestClient) -> None:
    body = scenario(client, "service_outside_collection")

    assert body["service"] == {
        "state": "outside_collection",
        "message": "지금은 예보 시간이 아니에요",
        "nextForecastStartAt": "2026-10-08T05:45:00+09:00",
    }
    assert body["buses"] == []
    assert body["alternatives"]["status"] == "undecidable"
    assert body["alternatives"]["candidates"] == []
    assert body["nextRefreshAt"] == "2026-10-08T05:45:00+09:00"


def test_service_outside_forecast_hours(client: TestClient) -> None:
    body = scenario(client, "service_outside_forecast_hours")

    assert body["service"]["state"] == "outside_forecast_hours"
    assert body["service"]["message"] == "지금은 예보 시간이 아니에요"
    assert body["service"]["nextForecastStartAt"] == "2026-10-08T05:45:00+09:00"
    assert body["nextRefreshAt"] == body["service"]["nextForecastStartAt"]
    assert all(bus["inForecastHours"] is False for bus in body["buses"])


@pytest.mark.parametrize("name", SNAPSHOT_SCENARIOS)
def test_every_scenario_is_consistent_with_rules(client: TestClient, name: str) -> None:
    body = scenario(client, name)

    for bus, candidate in zip(body["buses"], body["alternatives"]["candidates"], strict=True):
        leads = [f["leadTimeMin"] for f in bus["forecasts"]]
        assert leads == sorted(FORECAST_RULES.lead_times_min, reverse=True)
        assert candidate["leadTimeMin"] == bus["selectedLeadTimeMin"]
        chosen = selected(bus)
        assert candidate["noSeatProbability"] == (chosen["noSeatProbability"] if chosen else None)
        for forecast in bus["forecasts"]:
            if forecast["status"] == "not_yet":
                assert forecast["issuedAt"] is None
            if forecast["status"] == "ok":
                n, k = forecast["n"], forecast["k"]
                assert n >= FORECAST_RULES.min_cases_to_show
                assert forecast["noSeatProbability"] == round(k / n, 4)
                assert forecast["riskLevel"] == risk_level(k / n)
            else:
                assert forecast["noSeatProbability"] is None


# ---------------------------------------------------------------------------
# 검증 실패·없음
# ---------------------------------------------------------------------------
def test_missing_station_is_400(client: TestClient) -> None:
    response = client.get(SNAPSHOT, params={"destination": DESTINATION})

    body = assert_error(response, 400, "VALIDATION_FAILED")
    assert body["error"]["details"] == [{"field": "station", "reason": "필수 값입니다"}]


@pytest.mark.parametrize(
    ("params", "field"),
    [
        ({"station": "abc"}, "station"),
        ({"station": "1" * 21}, "station"),
        ({"destination": "Jamsil"}, "destination"),
        ({"deadline": "24:00"}, "deadline"),
        ({"deadline": "8:30"}, "deadline"),
    ],
)
def test_bad_format_is_400(client: TestClient, params: dict[str, str], field: str) -> None:
    body = assert_error(get_snapshot(client, **params), 400, "VALIDATION_FAILED")

    assert [d["field"] for d in body["error"]["details"]] == [field]


def test_bad_deadline_reason_matches_contract(client: TestClient) -> None:
    body = assert_error(get_snapshot(client, deadline="25:61"), 400, "VALIDATION_FAILED")

    assert body["error"] == {
        "code": "VALIDATION_FAILED",
        "message": "요청 형식이 올바르지 않습니다",
        "details": [{"field": "deadline", "reason": "HH:MM 형식(00:00–23:59)이어야 합니다"}],
    }


def test_error_does_not_echo_input(client: TestClient) -> None:
    response = get_snapshot(client, station="<script>alert(1)</script>")

    assert_error(response, 400, "VALIDATION_FAILED")
    assert "script" not in response.text


@pytest.mark.parametrize("params", [{"station": "999999999"}, {"destination": "busan"}])
def test_unknown_station_or_destination_is_404(client: TestClient, params: dict[str, str]) -> None:
    assert_error(get_snapshot(client, **params), 404, "NOT_FOUND")


def test_unknown_scenario_is_400_when_fake_data(client: TestClient) -> None:
    body = assert_error(get_snapshot(client, scenario="nope"), 400, "VALIDATION_FAILED")

    assert body["error"]["details"] == [{"field": "scenario", "reason": "허용된 값이 아닙니다"}]


def test_scenario_is_ignored_when_fake_data_false(app_factory: AppFactory) -> None:
    app = app_factory(fake_data=False)
    # DB 저장소가 아직 없어 가짜 저장소를 직접 넣고, scenario 가 무시되는지만 본다.
    app.dependency_overrides[get_repository] = FakeRepository
    client = TestClient(app, raise_server_exceptions=False)

    for name in ("nope", "status_stale"):
        response = get_snapshot(client, scenario=name)
        assert response.status_code == 200
        assert response.json() == contract_example_snapshot()


def test_fake_data_false_without_db_repository_is_500(app_factory: AppFactory) -> None:
    client = TestClient(app_factory(fake_data=False), raise_server_exceptions=False)

    response = get_snapshot(client)

    body = assert_error(response, 500, "INTERNAL_ERROR")
    assert body["error"]["details"] == []
    # 내부 예외 내용을 응답에 담지 않는다.
    assert "FAKE_DATA" not in response.text
    assert "RuntimeError" not in response.text
