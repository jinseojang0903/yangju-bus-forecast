import json

import httpx

from app.collector.gbis import GbisClient, as_list, parse_body
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


def test_parse_empty_response_is_not_failure() -> None:
    # 운행하지 않는 시간의 P 노선 등: HTTP 200 + msgHeader·msgBody 없는 response.
    data = {"response": {"comMsgHeader": ""}}
    parsed = parse_body(json.dumps(data), LOCATION_KEY)
    assert parsed.ok is True and parsed.is_empty is True and parsed.is_no_result is True
    assert parsed.error is None and parsed.items == [] and parsed.result_code is None
    assert parsed.body == data  # 원본 그대로
    assert parse_body(json.dumps({"response": {}}), LOCATION_KEY).is_empty is True
    # 게이트웨이 오류(JSON)는 빈 응답이 아니라 실패다.
    gateway_in_response = json.dumps(
        {"response": {"cmmMsgHeader": {"returnAuthMsg": "SERVICE_KEY_IS_NOT_REGISTERED_ERROR"}}}
    )
    gateway = parse_body(gateway_in_response, LOCATION_KEY)
    assert gateway.ok is False and gateway.is_empty is False
    top_level = json.dumps(
        {"OpenAPI_ServiceResponse": {"cmmMsgHeader": {"errMsg": "SERVICE ERROR"}}}
    )
    assert parse_body(top_level, LOCATION_KEY).ok is False
    # 결과 없음(resultCode 4)은 빈 응답과 구분한다.
    no_result = parse_body(load_fixture("bus_location_no_result_synthetic.json"), LOCATION_KEY)
    assert no_result.is_no_result is True and no_result.is_empty is False


def test_parse_gateway_quota_exceeded_is_detected() -> None:
    base = load_fixture("gateway_error_synthetic.xml")
    by_marker = base.replace(
        "SERVICE_KEY_IS_NOT_REGISTERED_ERROR", "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR"
    )
    parsed = parse_body(by_marker, LOCATION_KEY)
    assert parsed.ok is False and parsed.is_quota_exceeded is True
    assert parsed.error is not None and parsed.error.startswith("하루 호출량 초과")

    by_code = base.replace(
        "<returnReasonCode>30</returnReasonCode>", "<returnReasonCode>22</returnReasonCode>"
    )
    assert parse_body(by_code, LOCATION_KEY).is_quota_exceeded is True
    assert parse_body(base, LOCATION_KEY).is_quota_exceeded is False

    portal_json = json.dumps(
        {"response": {"header": {"resultCode": "22", "resultMsg": "LIMITED NUMBER"}}}
    )
    assert parse_body(portal_json, LOCATION_KEY).is_quota_exceeded is True
    # GBIS 자체 resultCode 22 는 호출량 초과로 보지 않는다(뜻이 확인되지 않았다).
    gbis_json = json.dumps({"response": {"msgHeader": {"resultCode": 22, "resultMessage": "x"}}})
    assert parse_body(gbis_json, LOCATION_KEY).is_quota_exceeded is False
    assert (
        parse_body(load_fixture("bus_location_ok_synthetic.json"), LOCATION_KEY).is_quota_exceeded
        is False
    )


def test_client_passes_quota_flag_and_uses_timeout() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        text = load_fixture("gateway_error_synthetic.xml").replace(
            "SERVICE_KEY_IS_NOT_REGISTERED_ERROR",
            "LIMITED_NUMBER_OF_SERVICE_REQUESTS_EXCEEDS_ERROR",
        )
        return httpx.Response(200, text=text)

    settings = make_settings()
    client = make_client(handler, settings, Redactor(settings.secret_values()))
    result = client.call(GBIS_BUS_LOCATION, {"routeId": "900000001"})
    assert result.ok is False and result.is_quota_exceeded is True

    timed = GbisClient(FAKE_KEY, Redactor(), timeout_sec=8.0)
    try:
        assert timed._http.timeout.read == 8.0 and timed._http.timeout.connect == 8.0
    finally:
        timed.close()


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
