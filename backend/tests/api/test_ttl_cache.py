"""TtlCache 키별 single-flight: 같은 키는 compute 1회, 기다린 요청은 결과·예외를 함께 쓴다."""

import threading
import time
from collections.abc import Callable
from typing import Any

import pytest

from app.services.cache import TtlCache
from tests.api.helpers import FakeClock

WAITERS = 5
# 기다리는 스레드들이 get_or_set 안으로 들어올 시간. 실패하면 single-flight 가 깨진 것이다.
SETTLE_SEC = 0.2


class BlockingCompute:
    """release 가 설정될 때까지 막혀 있는 compute. 호출 수를 센다."""

    def __init__(self, result: Any = "value", error: Exception | None = None) -> None:
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self._result = result
        self._error = error
        self._lock = threading.Lock()

    def __call__(self) -> Any:
        with self._lock:
            self.calls += 1
        self.entered.set()
        assert self.release.wait(5), "테스트가 compute 를 풀어 주지 않았다"
        if self._error is not None:
            raise self._error
        return self._result


def run_concurrently(
    cache: TtlCache[Any],
    compute: BlockingCompute,
    ttl_for: Callable[[Any], float | None] | None = None,
) -> list[Any]:
    """WAITERS 개 스레드가 같은 키로 get_or_set 을 부른다. 결과(값 또는 예외) 목록."""
    outcomes: list[Any] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            value: Any = cache.get_or_set("key", compute, ttl_for)
        except Exception as exc:
            value = exc
        with lock:
            outcomes.append(value)

    threads = [threading.Thread(target=worker) for _ in range(WAITERS)]
    for thread in threads:
        thread.start()
    assert compute.entered.wait(5)
    time.sleep(SETTLE_SEC)
    compute.release.set()
    for thread in threads:
        thread.join(5)
    return outcomes


def test_concurrent_calls_compute_once_and_share_value() -> None:
    cache: TtlCache[Any] = TtlCache(ttl_sec=10, clock=FakeClock())
    result = object()
    compute = BlockingCompute(result=result)

    outcomes = run_concurrently(cache, compute)

    assert compute.calls == 1
    assert len(outcomes) == WAITERS
    assert all(outcome is result for outcome in outcomes)
    # 저장된 값은 다음 호출에서 그대로 쓴다
    assert cache.get_or_set("key", lambda: "other") is result


def test_concurrent_calls_share_error_and_do_not_cache_it() -> None:
    cache: TtlCache[Any] = TtlCache(ttl_sec=10, clock=FakeClock())
    error = RuntimeError("boom")
    compute = BlockingCompute(error=error)

    outcomes = run_concurrently(cache, compute)

    assert compute.calls == 1
    assert all(outcome is error for outcome in outcomes)
    assert cache.get_or_set("key", lambda: "after") == "after"


def test_uncacheable_value_is_shared_by_waiters_but_not_stored() -> None:
    cache: TtlCache[Any] = TtlCache(ttl_sec=10, clock=FakeClock())
    compute = BlockingCompute(result=None)

    outcomes = run_concurrently(cache, compute, ttl_for=lambda value: None)

    assert compute.calls == 1
    assert outcomes == [None] * WAITERS
    assert cache.get_or_set("key", lambda: "after", lambda value: None) == "after"


def test_other_keys_are_not_blocked() -> None:
    cache: TtlCache[Any] = TtlCache(ttl_sec=10, clock=FakeClock())
    slow = BlockingCompute(result="slow")
    thread = threading.Thread(target=lambda: cache.get_or_set("slow", slow))
    thread.start()
    assert slow.entered.wait(5)
    try:
        assert cache.get_or_set("fast", lambda: "fast") == "fast"
    finally:
        slow.release.set()
        thread.join(5)


def test_expired_value_is_recomputed() -> None:
    clock = FakeClock()
    cache: TtlCache[str] = TtlCache(ttl_sec=10, clock=clock)
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return f"v{len(calls)}"

    assert cache.get_or_set("key", compute) == "v1"
    clock.advance(9.9)
    assert cache.get_or_set("key", compute) == "v1"
    clock.advance(0.1)
    assert cache.get_or_set("key", compute) == "v2"


@pytest.mark.parametrize("ttl", [0, -1])
def test_non_positive_ttl_is_not_stored(ttl: float) -> None:
    cache: TtlCache[str] = TtlCache(ttl_sec=10, clock=FakeClock())
    calls: list[int] = []

    def compute() -> str:
        calls.append(1)
        return "v"

    cache.get_or_set("key", compute, lambda value: ttl)
    cache.get_or_set("key", compute, lambda value: ttl)

    assert len(calls) == 2
