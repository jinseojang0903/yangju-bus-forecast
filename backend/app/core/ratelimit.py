"""IP 별 호출 횟수 제한(계약 1.1절). 프로세스 메모리에만 둔다.

이동 창(sliding log) 방식: 키마다 최근 호출 시각을 창 길이만큼만 보관한다.
서버를 여러 프로세스로 띄우면 프로세스마다 따로 센다(지금 배포는 1개).
클라이언트 키(IPv6 /64 묶기 등)는 호출하는 쪽(app/api/deps.py)이 정한다.
"""

import logging
import math
import threading
import time
from collections import OrderedDict, deque
from collections.abc import Callable, Sequence

from app.core.settings import API_RATE_LIMITS, RateLimitRule

logger = logging.getLogger(__name__)

# 이만큼 호출할 때마다 만료된 키를 한 번 정리한다.
_PRUNE_EVERY_CALLS = 1_000

_Key = tuple[str, int, str]


class RateLimiter:
    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        max_tracked_keys: int = API_RATE_LIMITS.max_tracked_keys,
    ) -> None:
        self._clock = clock
        self._max_tracked_keys = max_tracked_keys
        # (경로 묶음, 규칙 순번, 클라이언트) → (창 길이, 최근 호출 시각).
        # 최근에 쓴 키가 뒤에 오도록 유지해, 넘치면 가장 오래 안 쓴 키부터 내보낸다.
        self._hits: OrderedDict[_Key, tuple[int, deque[float]]] = OrderedDict()
        self._lock = threading.Lock()
        self._calls_since_prune = 0

    @property
    def tracked_keys(self) -> int:
        return len(self._hits)

    def hit(self, bucket: str, rules: Sequence[RateLimitRule], client_key: str) -> int | None:
        """호출 1회를 센다. 허용이면 None, 초과면 다시 시도할 수 있을 때까지의 초(1 이상).

        초과한 호출은 세지 않는다. 규칙이 여럿이면(분당·하루) 모두 통과해야 허용이다.
        """
        now = self._clock()
        with self._lock:
            windows: list[deque[float]] = []
            retry_after = 0
            for index, rule in enumerate(rules):
                key = (bucket, index, client_key)
                _, hits = self._hits.setdefault(key, (rule.window_sec, deque()))
                self._hits.move_to_end(key)
                cutoff = now - rule.window_sec
                while hits and hits[0] <= cutoff:
                    hits.popleft()
                if len(hits) >= rule.limit:
                    wait = hits[len(hits) - rule.limit] + rule.window_sec - now
                    retry_after = max(retry_after, math.ceil(wait), 1)
                windows.append(hits)
            if retry_after:
                return retry_after
            for hits in windows:
                hits.append(now)
            self._calls_since_prune += 1
            if (
                self._calls_since_prune >= _PRUNE_EVERY_CALLS
                or len(self._hits) > self._max_tracked_keys
            ):
                self._prune(now)
            return None

    def _prune(self, now: float) -> None:
        self._calls_since_prune = 0
        expired = []
        for key, (window_sec, hits) in self._hits.items():
            cutoff = now - window_sec
            while hits and hits[0] <= cutoff:
                hits.popleft()
            if not hits:
                expired.append(key)
        for key in expired:
            del self._hits[key]
        overflow = len(self._hits) - self._max_tracked_keys
        if overflow > 0:
            # 서로 다른 클라이언트가 비정상적으로 많다. 메모리를 지키려고 가장 오래 안 쓴 키부터
            # 내보낸다. 최근에 쓴 키(예: 진행 중인 하루 제한 기록)는 남는다.
            logger.warning("rate_limit_evict evicted=%d tracked_keys=%d", overflow, len(self._hits))
            for _ in range(overflow):
                self._hits.popitem(last=False)
