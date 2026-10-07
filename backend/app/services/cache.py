"""짧은 TTL 메모리 캐시(스냅샷 10초 캐시, 계약 1.3절).

가짜 저장소는 정적 응답이라 캐시가 결과를 바꾸지 않는다. DB 저장소를 붙이면 같은 자리에서
(정류장, 목적지, 마감) 조합별로 계산 결과를 10초 재사용한다.
"""

import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Hashable

from app.core.settings import SNAPSHOT_CACHE_MAX_ENTRIES, SNAPSHOT_CACHE_TTL_SEC


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
        """
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
        value = compute()
        ttl: float | None = self._ttl_sec
        if ttl_for is not None:
            limit = ttl_for(value)
            ttl = None if limit is None else min(self._ttl_sec, limit)
        if ttl is None or ttl <= 0:
            return value
        with self._lock:
            self._entries.pop(key, None)
            if len(self._entries) >= self._max_entries:
                self._evict_expired(now)
            while len(self._entries) >= self._max_entries:
                self._entries.popitem(last=False)
            self._entries[key] = (now + ttl, value)
        return value

    def _evict_expired(self, now: float) -> None:
        expired = [key for key, (expires_at, _) in self._entries.items() if expires_at <= now]
        for key in expired:
            del self._entries[key]
