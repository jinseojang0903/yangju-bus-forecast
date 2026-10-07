"""짧은 TTL 메모리 캐시(스냅샷·노선 지도 10초 캐시, 계약 1.3절·4.8절).

가짜 저장소는 정적 응답이라 캐시가 결과를 바꾸지 않는다. DB 저장소를 붙이면 같은 자리에서
(정류장, 목적지, 마감) 조합별로 계산 결과를 10초 재사용한다.

키별 single-flight: 같은 키의 값이 없을 때 동시에 들어온 요청 중 하나만 compute 하고,
나머지는 그 결과(또는 예외)를 기다렸다 함께 쓴다. 캐시가 비는 순간 몰린 요청이 파일 읽기·계산을
여러 번 하지 않게 하려는 것이다. FastAPI 동기 경로는 스레드 풀에서 돌므로 스레드 잠금을 쓴다.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable
from typing import Any

from app.core.settings import SNAPSHOT_CACHE_MAX_ENTRIES, SNAPSHOT_CACHE_TTL_SEC


class _Flight:
    """진행 중인 compute 1건. 끝나면 done 이 설정되고 value 나 error 중 하나가 채워진다."""

    def __init__(self) -> None:
        self.done = threading.Event()
        self.value: Any = None
        self.error: BaseException | None = None


class TtlCache[T]:
    def __init__(
        self,
        ttl_sec: float = SNAPSHOT_CACHE_TTL_SEC,
        max_entries: int = SNAPSHOT_CACHE_MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._ttl_sec = ttl_sec
        self._max_entries = max_entries
        self._clock = clock
        # 넣은 순서(= 만료가 가까운 순서에 가깝다)로 두고, 넘치면 오래된 것부터 뺀다.
        self._entries: OrderedDict[Hashable, tuple[float, T]] = OrderedDict()
        self._in_flight: dict[Hashable, _Flight] = {}
        self._lock = threading.Lock()

    def get_or_set(
        self,
        key: Hashable,
        compute: Callable[[], T],
        ttl_for: Callable[[T], float | None] | None = None,
    ) -> T:
        """캐시에 살아 있는 값이 있으면 그것을, 없으면 compute() 결과를 돌려준다.

        ttl_for(value) 가 있으면 그 값(초)을 기본 TTL 과 비교해 짧은 쪽으로 저장한다.
        ttl_for 가 None 을 돌려주거나 0 이하이면 저장하지 않는다(바로 만료).
        compute 가 예외를 내면 저장하지 않고 그대로 올린다.
        같은 키를 이미 다른 요청이 compute 하는 중이면 기다렸다 그 결과를 돌려주거나
        그 예외를 올린다(저장하지 않는 결과도 그 순간 기다린 요청끼리는 함께 쓴다).
        기다리는 동안 스레드를 막으므로 동기(def) 라우트에서만 부른다. async 경로에서는
        run_in_threadpool 로 감싼다.
        """
        with self._lock:
            now = self._clock()
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
            flight = self._in_flight.get(key)
            is_leader = flight is None
            if flight is None:
                flight = _Flight()
                self._in_flight[key] = flight

        if not is_leader:
            flight.done.wait()
            if flight.error is not None:
                raise flight.error
            return flight.value

        try:
            value = compute()
            flight.value = value
            self._store(key, value, ttl_for, now)
            return value
        except BaseException as exc:
            flight.error = exc
            raise
        finally:
            with self._lock:
                self._in_flight.pop(key, None)
            flight.done.set()

    def _store(
        self,
        key: Hashable,
        value: T,
        ttl_for: Callable[[T], float | None] | None,
        now: float,
    ) -> None:
        ttl: float | None = self._ttl_sec
        if ttl_for is not None:
            limit = ttl_for(value)
            ttl = None if limit is None else min(self._ttl_sec, limit)
        if ttl is None or ttl <= 0:
            return
        with self._lock:
            self._entries.pop(key, None)
            if len(self._entries) >= self._max_entries:
                self._evict_expired(now)
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
            self._entries[key] = (now + ttl, value)

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            del self._entries[key]
