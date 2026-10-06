import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.deps import client_key
from app.core.ratelimit import RateLimiter
from app.core.settings import API_RATE_LIMITS, RateLimitRule
from tests.api.helpers import API, DESTINATION, STATION, AppFactory, FakeClock, assert_error

SNAPSHOT_PARAMS = {"station": STATION, "destination": DESTINATION}


def limited_client(app_factory: AppFactory) -> tuple[TestClient, FakeClock]:
    clock = FakeClock()
    app: FastAPI = app_factory()
    app.state.rate_limiter = RateLimiter(clock=clock)
    return TestClient(app, raise_server_exceptions=False), clock


# ---------------------------------------------------------------------------
# RateLimiter 단위
# ---------------------------------------------------------------------------
def test_limiter_allows_up_to_limit_then_returns_retry_after() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    rules = (RateLimitRule(limit=3, window_sec=60),)

    assert [limiter.hit("b", rules, "1.2.3.4") for _ in range(3)] == [None, None, None]
    clock.advance(10)
    assert limiter.hit("b", rules, "1.2.3.4") == 50
    # 다른 IP·다른 묶음은 따로 센다
    assert limiter.hit("b", rules, "5.6.7.8") is None
    assert limiter.hit("other", rules, "1.2.3.4") is None
    clock.advance(50)
    assert limiter.hit("b", rules, "1.2.3.4") is None


def test_limiter_does_not_count_rejected_calls() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock)
    rules = (RateLimitRule(limit=1, window_sec=60),)

    assert limiter.hit("b", rules, "ip") is None
    for _ in range(5):
        clock.advance(1)
        assert limiter.hit("b", rules, "ip") is not None
    clock.advance(55)
    assert limiter.hit("b", rules, "ip") is None


def test_limiter_evicts_oldest_keys_when_too_many() -> None:
    clock = FakeClock()
    limiter = RateLimiter(clock=clock, max_tracked_keys=10)
    daily = (RateLimitRule(limit=1, window_sec=86_400),)

    assert limiter.hit("daily", daily, "keeper") is None
    for index in range(30):
        assert limiter.hit("b", daily, f"ip-{index}") is None
        # keeper 는 계속 쓰이므로(거절도 사용으로 본다) 가장 오래된 키가 아니다
        assert limiter.hit("daily", daily, "keeper") is not None

    assert limiter.tracked_keys <= 10
    # 전부 비우지 않았으므로 keeper 의 하루 기록이 남아 있다
    assert limiter.hit("daily", daily, "keeper") is not None
    # 오래된 키는 내보내져 다시 허용된다
    assert limiter.hit("b", daily, "ip-0") is None


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("203.0.113.7", "203.0.113.7"),
        ("2001:db8:1:2:aaaa:bbbb:cccc:dddd", "2001:db8:1:2::/64"),
        ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
        ("::ffff:203.0.113.7", "203.0.113.7"),
        ("testclient", "testclient"),
        (None, "unknown"),
    ],
)
def test_client_key_groups_ipv6_by_64(host: str | None, expected: str) -> None:
    assert client_key(host) == expected


# ---------------------------------------------------------------------------
# 경로별 제한(계약 1.1절)
# ---------------------------------------------------------------------------
def test_snapshot_limit_returns_429_with_retry_after(app_factory: AppFactory) -> None:
    client, clock = limited_client(app_factory)
    limit = API_RATE_LIMITS.snapshot[0].limit

    for _ in range(limit):
        assert client.get(f"{API}/snapshot", params=SNAPSHOT_PARAMS).status_code == 200
    response = client.get(f"{API}/snapshot", params=SNAPSHOT_PARAMS)

    assert_error(response, 429, "RATE_LIMITED")
    assert response.headers["Retry-After"] == "60"
    clock.advance(60)
    assert client.get(f"{API}/snapshot", params=SNAPSHOT_PARAMS).status_code == 200


def test_invalid_requests_also_count(app_factory: AppFactory) -> None:
    client, _ = limited_client(app_factory)
    limit = API_RATE_LIMITS.snapshot[0].limit

    for _ in range(limit):
        assert client.get(f"{API}/snapshot").status_code == 400

    assert_error(client.get(f"{API}/snapshot", params=SNAPSHOT_PARAMS), 429, "RATE_LIMITED")


def test_explanation_limit(app_factory: AppFactory) -> None:
    client, _ = limited_client(app_factory)
    snapshot_id = client.get(f"{API}/snapshot", params=SNAPSHOT_PARAMS).json()["snapshotId"]
    url = f"{API}/snapshot/{snapshot_id}/explanation"

    for _ in range(API_RATE_LIMITS.explanation[0].limit):
        assert client.get(url).status_code == 200

    assert_error(client.get(url), 429, "RATE_LIMITED")


def test_parse_query_minute_and_daily_limits(app_factory: AppFactory) -> None:
    client, clock = limited_client(app_factory)
    per_minute, per_day = API_RATE_LIMITS.parse_query
    url = f"{API}/parse-query"

    for _ in range(per_minute.limit):
        assert client.post(url, json={"text": "잠실"}).status_code == 200
    assert_error(client.post(url, json={"text": "잠실"}), 429, "RATE_LIMITED")

    sent = per_minute.limit
    while sent < per_day.limit:
        clock.advance(per_minute.window_sec + 1)
        for _ in range(min(per_minute.limit, per_day.limit - sent)):
            assert client.post(url, json={"text": "잠실"}).status_code == 200
            sent += 1
    clock.advance(per_minute.window_sec + 1)
    response = client.post(url, json={"text": "잠실"})

    assert_error(response, 429, "RATE_LIMITED")
    assert int(response.headers["Retry-After"]) > per_minute.window_sec


def test_other_gets_share_one_limit(app_factory: AppFactory) -> None:
    client, _ = limited_client(app_factory)
    limit = API_RATE_LIMITS.default_get[0].limit

    for index in range(limit):
        path = "/stations" if index % 2 else "/health"
        assert client.get(f"{API}{path}").status_code == 200

    assert_error(client.get(f"{API}/stations/{STATION}/routes"), 429, "RATE_LIMITED")
    # 스냅샷은 별도 한도
    assert client.get(f"{API}/snapshot", params=SNAPSHOT_PARAMS).status_code == 200
