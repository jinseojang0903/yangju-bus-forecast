import pytest
from fastapi.testclient import TestClient

from tests.api.helpers import API, assert_error

PARSE_QUERY = f"{API}/parse-query"


def test_parse_query_is_unavailable(client: TestClient) -> None:
    response = client.post(PARSE_QUERY, json={"text": "덕현초교에서 잠실 8시 반까지"})

    assert response.status_code == 200
    assert response.json() == {
        "status": "unavailable",
        "fallbackReason": "llm_disabled",
        "query": {"station": None, "destination": None, "deadline": None},
        "message": "지금은 질문으로 조회할 수 없어요. 정류장과 목적지를 직접 골라 주세요.",
    }


def test_parse_query_accepts_200_chars(client: TestClient) -> None:
    assert client.post(PARSE_QUERY, json={"text": "가" * 200}).status_code == 200


@pytest.mark.parametrize("text", ["", "   ", "가" * 201])
def test_parse_query_bad_text_is_400(client: TestClient, text: str) -> None:
    response = client.post(PARSE_QUERY, json={"text": text})

    body = assert_error(response, 400, "VALIDATION_FAILED")
    assert body["error"]["details"] == [{"field": "text", "reason": "1–200자여야 합니다"}]
    if text.strip():
        assert text not in response.text


def test_parse_query_missing_text_is_400(client: TestClient) -> None:
    body = assert_error(client.post(PARSE_QUERY, json={}), 400, "VALIDATION_FAILED")

    assert body["error"]["details"] == [{"field": "text", "reason": "필수 값입니다"}]


def test_parse_query_invalid_json_is_400(client: TestClient) -> None:
    response = client.post(
        PARSE_QUERY, content=b"{not json", headers={"Content-Type": "application/json"}
    )

    assert_error(response, 400, "VALIDATION_FAILED")


def test_parse_query_get_is_405(client: TestClient) -> None:
    assert_error(client.get(PARSE_QUERY), 405, "METHOD_NOT_ALLOWED")
