import json

import httpx

from app.collector.gbis import as_list, parse_body
from app.collector.redact import Redactor
from app.collector.summary import field_presence, summarize, vehicle_examples
from app.core.settings import GBIS_BUS_ARRIVAL, GBIS_BUS_LOCATION
from tests.collector.helpers import FAKE_KEY, load_fixture, make_client, make_settings

LOCATION_KEY = GBIS_BUS_LOCATION.list_key


def test_parse_ok_list() -> None:
    parsed = parse_body(load_fixture("bus_location_ok_synthetic.json"), LOCATION_KEY)
    assert parsed.ok is True
    assert parsed.result_code == "0"
    assert parsed.error is None
    assert len(parsed.items) == 2
    assert isinstance(parsed.body, dict)
    assert parsed.items[0]["stateCd"] == 2


def test_parse_single_item_dict_becomes_list() -> None:
    parsed = parse_body(load_fixture("bus_location_single_dict_synthetic.json"), LOCATION_KEY)
    assert parsed.ok is True
    assert parsed.result_code == "0"  # 문자열 "0" 도 같은 코드로 본다
    assert isinstance(parsed.items, list) and len(parsed.items) == 1
    assert parsed.items[0]["vehId"] == 900000903


def test_parse_no_result_is_not_failure() -> None:
    parsed = parse_body(load_fixture("bus_location_no_result_synthetic.json"), LOCATION_KEY)
    assert parsed.ok is True
    assert parsed.is_no_result is True
    assert parsed.result_code == "4"
    assert parsed.items == []
    assert parsed.error is None


def test_parse_gateway_xml_error_is_failure() -> None:
    text = load_fixture("gateway_error_synthetic.xml")
    parsed = parse_body(text, LOCATION_KEY)
    assert parsed.ok is False
    assert parsed.error is not None and "SERVICE_KEY_IS_NOT_REGISTERED_ERROR" in parsed.error
    assert parsed.result_code == "30"
    assert parsed.body == text  # 원문 그대로 남긴다


def test_parse_unknown_result_code_is_failure() -> None:
    text = json.dumps({"response": {"msgHeader": {"resultCode": 8, "resultMessage": "요청 제한"}}})
    parsed = parse_body(text, LOCATION_KEY)
    assert parsed.ok is False
    assert parsed.result_code == "8"
    assert parsed.error is not None and "resultCode=8" in parsed.error


def test_parse_missing_header_and_non_json() -> None:
    assert parse_body(json.dumps({"foo": 1}), LOCATION_KEY).ok is False
    plain = parse_body("Service Unavailable", LOCATION_KEY)
    assert plain.ok is False and plain.body == "Service Unavailable"


def test_parse_list_key_fallback() -> None:
    text = json.dumps(
        {
            "response": {
                "msgHeader": {"resultCode": 0, "resultMessage": "ok"},
                "msgBody": {"otherName": [{"vehId": 1}]},
            }
        }
    )
    assert parse_body(text, LOCATION_KEY).items == [{"vehId": 1}]


def test_as_list() -> None:
    assert as_list({"a": 1}) == [{"a": 1}]
    assert as_list([{"a": 1}, "x"]) == [{"a": 1}]
    assert as_list(None) == []


def test_client_sends_key_and_json_format_but_result_params_exclude_key() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=load_fixture("bus_location_ok_synthetic.json"))

    settings = make_settings()
    client = make_client(handler, settings, Redactor(settings.secret_values()))
    result = client.call(GBIS_BUS_LOCATION, {"routeId": "900000001"})

    assert seen[0].url.params["serviceKey"] == FAKE_KEY
    assert seen[0].url.params["format"] == "json"
    assert result.params == {"routeId": "900000001", "format": "json"}
    assert result.ok is True and result.http_status == 200 and len(result.items) == 2


def test_client_http_error_status_is_failure() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text=load_fixture("gateway_error_synthetic.xml"))

    settings = make_settings()
    client = make_client(handler, settings, Redactor(settings.secret_values()))
    result = client.call(GBIS_BUS_ARRIVAL, {"stationId": "900000105"})
    assert result.ok is False
    assert result.http_status == 500
    assert result.error is not None and result.error.startswith("HTTP 500")


def test_summary_reports_fields_and_vehicle_ids() -> None:
    parsed = parse_body(load_fixture("bus_arrival_ok_synthetic.json"), GBIS_BUS_ARRIVAL.list_key)
    presence = field_presence(parsed.items)
    assert presence["predictTimeSec1"] == "1/1"
    assert presence["stateCd"] == "0/1"
    assert vehicle_examples(parsed.items)["vehId1"] == ["900000901"]

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=load_fixture("bus_location_ok_synthetic.json"))

    settings = make_settings()
    redactor = Redactor(settings.secret_values())
    client = make_client(handler, settings, redactor)
    lines = summarize(client.call(GBIS_BUS_LOCATION, {"routeId": "1"}), "G1300", redactor)
    joined = "\n".join(lines)
    assert "stateCd=2/2" in joined and "remainSeatCnt=2/2" in joined
    assert "vehId" in joined
