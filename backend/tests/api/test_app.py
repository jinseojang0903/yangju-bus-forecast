"""공통 규칙: 에러 형식(404·405·500), 로그, 본문 상한, CORS, /docs·OpenAPI."""

import logging
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.api.deps import get_station_service
from app.core.settings import MAX_REQUEST_BODY_BYTES
from tests.api.helpers import API, AppFactory, assert_error

ALLOWED_ORIGIN = "https://yangju-bus.example.org"


def test_unknown_path_is_404(client: TestClient) -> None:
    body = assert_error(client.get(f"{API}/no-such-path"), 404, "NOT_FOUND")

    assert body["error"]["details"] == []


def test_wrong_method_is_405(client: TestClient) -> None:
    response = client.delete(f"{API}/stations")

    assert_error(response, 405, "METHOD_NOT_ALLOWED")
    assert "GET" in response.headers.get("Allow", "")


def test_unexpected_error_is_500_without_details(app_factory: AppFactory) -> None:
    app = app_factory()

    def broken_service() -> None:
        raise ValueError("secret internal detail C:\\path")

    app.dependency_overrides[get_station_service] = broken_service
    response = TestClient(app, raise_server_exceptions=False).get(f"{API}/stations")

    body = assert_error(response, 500, "INTERNAL_ERROR")
    assert body["error"]["details"] == []
    assert "secret" not in response.text
    assert "path" not in response.text


def test_internal_error_log_has_only_type_and_path(
    app_factory: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    app = app_factory()

    def broken_service() -> None:
        raise ValueError("secret internal detail")

    app.dependency_overrides[get_station_service] = broken_service
    caplog.set_level(logging.DEBUG, logger="app.core.errors")

    TestClient(app, raise_server_exceptions=False).get(f"{API}/stations")

    errors = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert errors, "500 은 error 로그를 남겨야 한다"
    for record in errors:
        assert "secret" not in record.getMessage()
        assert record.exc_info is None
        assert "ValueError" in record.getMessage()
        assert "/api/v1/stations" in record.getMessage()
    # 추적은 debug 에만 남는다
    assert any(r.levelno == logging.DEBUG and r.exc_info for r in caplog.records)


def test_fake_data_logs_warning_on_startup(
    app_factory: AppFactory, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING, logger="app.main")

    app_factory(fake_data=True)

    assert any("api_fake_data_enabled" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 본문 크기 상한(4096바이트)
# ---------------------------------------------------------------------------
def test_body_over_limit_is_400_before_reading(client: TestClient) -> None:
    body = b'{"text": "' + b"a" * MAX_REQUEST_BODY_BYTES + b'"}'

    response = client.post(
        f"{API}/parse-query", content=body, headers={"Content-Type": "application/json"}
    )

    result = assert_error(response, 400, "VALIDATION_FAILED")
    assert result["error"]["details"] == [{"field": "body", "reason": "본문이 너무 큽니다"}]


def test_streamed_body_over_limit_is_400(client: TestClient) -> None:
    def chunks() -> Iterator[bytes]:
        yield b'{"text": "'
        for _ in range(10):
            yield b"a" * 1000
        yield b'"}'

    response = client.post(
        f"{API}/parse-query", content=chunks(), headers={"Content-Type": "application/json"}
    )

    assert_error(response, 400, "VALIDATION_FAILED")


def test_body_within_limit_is_accepted(client: TestClient) -> None:
    response = client.post(f"{API}/parse-query", json={"text": "잠실"})

    assert response.status_code == 200


def test_cors_disallows_all_origins_by_default(client: TestClient) -> None:
    response = client.get(f"{API}/stations", headers={"Origin": ALLOWED_ORIGIN})

    assert response.status_code == 200
    assert "access-control-allow-origin" not in response.headers


def test_cors_allows_configured_origin_without_credentials(app_factory: AppFactory) -> None:
    client = TestClient(app_factory(cors_allow_origins=ALLOWED_ORIGIN))

    response = client.get(f"{API}/stations", headers={"Origin": ALLOWED_ORIGIN})
    preflight = client.options(
        f"{API}/parse-query",
        headers={
            "Origin": ALLOWED_ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "Content-Type",
        },
    )

    assert response.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert "access-control-allow-credentials" not in response.headers
    assert preflight.status_code == 200
    assert "POST" in preflight.headers["access-control-allow-methods"]
    assert "DELETE" not in preflight.headers["access-control-allow-methods"]


def test_cors_rejects_other_origin(app_factory: AppFactory) -> None:
    client = TestClient(app_factory(cors_allow_origins=ALLOWED_ORIGIN))

    response = client.get(f"{API}/stations", headers={"Origin": "https://evil.example.com"})

    assert "access-control-allow-origin" not in response.headers


def test_docs_and_openapi_are_served(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200

    schema = client.get("/openapi.json").json()

    paths = schema["paths"]
    assert "/api/v1/health/detail" not in paths
    for path in (
        "/api/v1/health",
        "/api/v1/stations",
        "/api/v1/stations/{stationId}/routes",
        "/api/v1/snapshot",
        "/api/v1/snapshot/{snapshotId}/explanation",
        "/api/v1/parse-query",
    ):
        assert path in paths
    for path_item in paths.values():
        for operation in path_item.values():
            assert "422" not in operation["responses"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "400" in paths["/api/v1/snapshot"]["get"]["responses"]
