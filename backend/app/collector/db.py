"""raw_poll DB 저장 자리.

이번 단계에서는 테이블 SQL 이 없어 비활성이다(settings.RAW_POLL_DB_SAVE_ENABLED = False).
수집기는 JSONL 을 먼저 쓰고 그다음 여기를 부른다. 여기서 실패해도 수집은 멈추지 않는다.
TODO(backend-dev/2026-10-06): backend/migrations 에 raw_poll 테이블을 만든 뒤
  PostgresRawPollSink.save 의 insert(파라미터 바인딩)와 JSONL→DB 재적재 스크립트를 구현한다.
"""

from typing import Any, Protocol

from app.core.settings import RAW_POLL_DB_SAVE_ENABLED, Settings


class RawPollSink(Protocol):
    def save(self, record: dict[str, Any]) -> None:
        """한 줄을 저장한다. 실패하면 예외를 올린다(호출하는 쪽이 잡아 기록한다)."""
        ...

    def close(self) -> None: ...


class PostgresRawPollSink:
    """psycopg 연결만 준비한다. insert 는 마이그레이션과 함께 구현한다."""

    def __init__(self, database_url: str) -> None:
        self._database_url = database_url
        self._conn: Any = None

    def _connect(self) -> Any:
        if self._conn is None or self._conn.closed:
            import psycopg  # DB 를 켜지 않으면 불러오지 않는다

            self._conn = psycopg.connect(self._database_url, connect_timeout=5, autocommit=True)
        return self._conn

    def save(self, record: dict[str, Any]) -> None:
        raise NotImplementedError("raw_poll 테이블 SQL 이 아직 없다")

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


def build_raw_poll_sink(settings: Settings) -> RawPollSink | None:
    """DB 저장이 켜져 있고 DATABASE_URL 이 있을 때만 sink 를 만든다.

    아니면 None 이다(파일에만 저장).
    """
    if not RAW_POLL_DB_SAVE_ENABLED or not settings.has_database_url:
        return None
    return PostgresRawPollSink(settings.database_url.get_secret_value().strip())
