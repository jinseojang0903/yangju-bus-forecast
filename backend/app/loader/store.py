"""적재 저장소. 트랜잭션 안의 쓰기 연산을 인터페이스(Writer)로 두어 테스트에서 가짜로 바꾼다.

PostgresLoaderStore 는 psycopg 로 SQL 을 직접 쓴다. 모든 SQL 은 고정 문자열이고 값은 파라미터
바인딩·COPY 로만 넘긴다. 접속 문자열은 로그·예외 메시지에 남기지 않는다(호출하는 쪽은 예외의
type 이름과 SQLSTATE 만 보여 준다).
"""

import os
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Protocol

from app.loader.reference import RouteRow, RouteStationRow, StationRow
from app.loader.rows import BusArrivalRow, BusPositionRow, RawPollRow, ServiceDayRow

# 운영값(규칙 값 아님). 서버 → Supabase 접속 대기 상한(초).
DB_CONNECT_TIMEOUT_SEC = 10
# 실제로 쓰일 sslmode(접속 문자열, 없으면 PGSSLMODE)가 이 값이면 그 값을,
# 없거나 약하면(disable·allow·prefer) require 를 명시한다.
_STRONG_SSLMODES = frozenset({"require", "verify-ca", "verify-full"})


class LoadError(RuntimeError):
    """적재 중 일관성이 깨졌다(예: 넣은 raw_poll 의 id 를 찾지 못함). 그날 적재를 되돌린다."""


@dataclass(frozen=True)
class RawPollInsert:
    ids: dict[int, int]  # line_no → raw_poll_id (새로 넣은 줄과 이미 있던 줄 모두)
    inserted: int  # 이번에 새로 넣은 줄 수


class Writer(Protocol):
    """한 트랜잭션 안의 쓰기. 예외가 나면 store.transaction() 이 전부 되돌린다."""

    def upsert_service_day(self, row: ServiceDayRow) -> None: ...

    def insert_raw_polls(self, jsonl_file: str, rows: Sequence[RawPollRow]) -> RawPollInsert:
        """(jsonl_file, line_no) 가 이미 있으면 넣지 않는다. 모든 줄의 id 를 돌려준다."""
        ...

    def insert_bus_positions(self, rows: Sequence[tuple[int, BusPositionRow]]) -> int:
        """(raw_poll_id, 행). 키가 이미 있으면 넣지 않는다. 새로 넣은 행 수."""
        ...

    def insert_bus_arrivals(self, rows: Sequence[tuple[int, BusArrivalRow]]) -> int: ...

    def upsert_routes(self, rows: Sequence[RouteRow]) -> int: ...

    def upsert_stations(self, rows: Sequence[StationRow]) -> int: ...

    def upsert_route_stations(self, rows: Sequence[RouteStationRow]) -> int: ...

    def delete_route_stations_except(self, route_id: str, keep_seqs: Sequence[int]) -> list[int]:
        """그 노선에서 keep_seqs 에 없는 순번 행을 지우고 지운 순번을 돌려준다.

        keep_seqs 가 비면 아무것도 지우지 않는다(빈 목록으로 노선 전체를 지우지 않게).
        """
        ...


class LoaderStore(Protocol):
    def transaction(self) -> Any:
        """with store.transaction() as writer: … 블록. 블록에서 예외가 나면 롤백 후 다시 올린다."""
        ...

    def close(self) -> None: ...


# ---------------------------------------------------------------------------
# PostgreSQL
# ---------------------------------------------------------------------------
_CREATE_TMP_RAW_POLL = """
CREATE TEMP TABLE loader_raw_poll (
    line_no       integer     NOT NULL,
    collected_at  timestamptz NOT NULL,
    api           text        NOT NULL,
    route_id      text,
    station_id    text,
    ok            boolean     NOT NULL,
    http_status   integer,
    result_code   text,
    mode          text        NOT NULL,
    interval_sec  integer,
    is_holiday    boolean     NOT NULL
) ON COMMIT DROP
"""
_COPY_TMP_RAW_POLL = """
COPY loader_raw_poll (line_no, collected_at, api, route_id, station_id, ok, http_status,
                      result_code, mode, interval_sec, is_holiday) FROM STDIN
"""
_INSERT_RAW_POLL = """
INSERT INTO public.raw_poll (jsonl_file, line_no, collected_at, api, route_id, station_id, ok,
                             http_status, result_code, mode, interval_sec, is_holiday)
SELECT %(jsonl_file)s::text, line_no, collected_at, api, route_id, station_id, ok,
       http_status, result_code, mode, interval_sec, is_holiday
FROM loader_raw_poll
ORDER BY line_no
ON CONFLICT (jsonl_file, line_no) DO NOTHING
"""
_SELECT_RAW_POLL_IDS = """
SELECT r.line_no, r.raw_poll_id
FROM public.raw_poll AS r
JOIN loader_raw_poll AS t ON t.line_no = r.line_no
WHERE r.jsonl_file = %(jsonl_file)s::text
"""

_CREATE_TMP_BUS_POSITION = """
CREATE TEMP TABLE loader_bus_position (
    raw_poll_id      bigint      NOT NULL,
    item_index       smallint    NOT NULL,
    collected_at     timestamptz NOT NULL,
    route_id         text        NOT NULL,
    veh_id           text,
    plate_no         text,
    station_seq      integer,
    station_id       text,
    state_cd         smallint,
    remain_seat_cnt  integer,
    crowded          smallint,
    interval_sec     integer,
    mode             text,
    is_holiday       boolean
) ON COMMIT DROP
"""
_COPY_TMP_BUS_POSITION = """
COPY loader_bus_position (raw_poll_id, item_index, collected_at, route_id, veh_id, plate_no,
                          station_seq, station_id, state_cd, remain_seat_cnt, crowded,
                          interval_sec, mode, is_holiday) FROM STDIN
"""
_INSERT_BUS_POSITION = """
INSERT INTO public.bus_position (raw_poll_id, item_index, collected_at, route_id, veh_id,
                                 plate_no, station_seq, station_id, state_cd, remain_seat_cnt,
                                 crowded, interval_sec, mode, is_holiday)
SELECT raw_poll_id, item_index, collected_at, route_id, veh_id, plate_no, station_seq,
       station_id, state_cd, remain_seat_cnt, crowded, interval_sec, mode, is_holiday
FROM loader_bus_position
ON CONFLICT (raw_poll_id, item_index) DO NOTHING
"""

_CREATE_TMP_BUS_ARRIVAL = """
CREATE TEMP TABLE loader_bus_arrival (
    raw_poll_id       bigint      NOT NULL,
    route_id          text        NOT NULL,
    arrival_rank      smallint    NOT NULL,
    item_index        smallint    NOT NULL,
    collected_at      timestamptz NOT NULL,
    station_id        text,
    veh_id            text,
    plate_no          text,
    predict_time_sec  integer,
    predict_time_min  integer,
    remain_seat_cnt   integer,
    location_no       integer,
    sta_order         integer,
    crowded           smallint,
    flag              text
) ON COMMIT DROP
"""
_COPY_TMP_BUS_ARRIVAL = """
COPY loader_bus_arrival (raw_poll_id, route_id, arrival_rank, item_index, collected_at,
                         station_id, veh_id, plate_no, predict_time_sec, predict_time_min,
                         remain_seat_cnt, location_no, sta_order, crowded, flag) FROM STDIN
"""
# 키는 기본키 bus_arrival_pk (raw_poll_id, item_index, arrival_rank) 다.
_INSERT_BUS_ARRIVAL = """
INSERT INTO public.bus_arrival (raw_poll_id, route_id, arrival_rank, item_index, collected_at,
                                station_id, veh_id, plate_no, predict_time_sec,
                                predict_time_min, remain_seat_cnt, location_no, sta_order,
                                crowded, flag)
SELECT raw_poll_id, route_id, arrival_rank, item_index, collected_at, station_id, veh_id,
       plate_no, predict_time_sec, predict_time_min, remain_seat_cnt, location_no, sta_order,
       crowded, flag
FROM loader_bus_arrival
ON CONFLICT (raw_poll_id, item_index, arrival_rank) DO NOTHING
"""

# 'none'(파일 없음)으로는 이미 기록된 regular·trial 을 덮지 않는다(파일을 옮긴 뒤 다시 돌려도
# 수집한 날이 '수집 없음'으로 바뀌지 않게). note 는 사람이 적는 열이라 건드리지 않는다.
_UPSERT_SERVICE_DAY = """
INSERT INTO public.service_day (service_date, is_weekday, is_holiday, operation_kind)
VALUES (%s, %s, %s, %s)
ON CONFLICT (service_date) DO UPDATE SET
    is_weekday = EXCLUDED.is_weekday,
    is_holiday = EXCLUDED.is_holiday,
    operation_kind = CASE
        WHEN EXCLUDED.operation_kind = 'none' THEN service_day.operation_kind
        ELSE EXCLUDED.operation_kind
    END,
    updated_at = now()
"""

# 노선 검색 기록에서 오는 열은 이번 기록에 없으면(NULL) 기존 값을 둔다.
_UPSERT_ROUTE = """
INSERT INTO public.route AS r (route_id, route_name, route_type_cd, route_type_name, admin_name,
                               region_name, start_station_name, end_station_name, is_collected,
                               is_label_target, is_reserved, is_night, collect_interval_sec,
                               note, source_date)
VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (route_id) DO UPDATE SET
    route_name = EXCLUDED.route_name,
    route_type_cd = COALESCE(EXCLUDED.route_type_cd, r.route_type_cd),
    route_type_name = COALESCE(EXCLUDED.route_type_name, r.route_type_name),
    admin_name = COALESCE(EXCLUDED.admin_name, r.admin_name),
    region_name = COALESCE(EXCLUDED.region_name, r.region_name),
    start_station_name = COALESCE(EXCLUDED.start_station_name, r.start_station_name),
    end_station_name = COALESCE(EXCLUDED.end_station_name, r.end_station_name),
    is_collected = EXCLUDED.is_collected,
    is_label_target = EXCLUDED.is_label_target,
    is_reserved = EXCLUDED.is_reserved,
    is_night = EXCLUDED.is_night,
    collect_interval_sec = EXCLUDED.collect_interval_sec,
    note = EXCLUDED.note,
    source_date = COALESCE(EXCLUDED.source_date, r.source_date),
    updated_at = now()
"""
# 이번 기록에 값이 없는 열(NULL)은 기존 값을 둔다.
_UPSERT_STATION = """
INSERT INTO public.station AS s (station_id, station_name, mobile_no, region_name, x, y,
                                 source_date)
VALUES (%s, %s, %s, %s, %s, %s, %s)
ON CONFLICT (station_id) DO UPDATE SET
    station_name = EXCLUDED.station_name,
    mobile_no = COALESCE(EXCLUDED.mobile_no, s.mobile_no),
    region_name = COALESCE(EXCLUDED.region_name, s.region_name),
    x = COALESCE(EXCLUDED.x, s.x),
    y = COALESCE(EXCLUDED.y, s.y),
    source_date = EXCLUDED.source_date,
    updated_at = now()
"""
_UPSERT_ROUTE_STATION = """
INSERT INTO public.route_station (route_id, station_seq, station_id, is_turn_point, source_date)
VALUES (%s, %s, %s, %s, %s)
ON CONFLICT (route_id, station_seq) DO UPDATE SET
    station_id = EXCLUDED.station_id,
    is_turn_point = EXCLUDED.is_turn_point,
    source_date = EXCLUDED.source_date,
    updated_at = now()
"""
_DELETE_STALE_ROUTE_STATIONS = """
DELETE FROM public.route_station
WHERE route_id = %s AND NOT (station_seq = ANY(%s::integer[]))
RETURNING station_seq
"""


def _raw_poll_values(row: RawPollRow) -> tuple[Any, ...]:
    return (
        row.line_no,
        row.collected_at,
        row.api,
        row.route_id,
        row.station_id,
        row.ok,
        row.http_status,
        row.result_code,
        row.mode,
        row.interval_sec,
        row.is_holiday,
    )


def _position_values(raw_poll_id: int, row: BusPositionRow) -> tuple[Any, ...]:
    return (
        raw_poll_id,
        row.item_index,
        row.collected_at,
        row.route_id,
        row.veh_id,
        row.plate_no,
        row.station_seq,
        row.station_id,
        row.state_cd,
        row.remain_seat_cnt,
        row.crowded,
        row.interval_sec,
        row.mode,
        row.is_holiday,
    )


def _arrival_values(raw_poll_id: int, row: BusArrivalRow) -> tuple[Any, ...]:
    return (
        raw_poll_id,
        row.route_id,
        row.arrival_rank,
        row.item_index,
        row.collected_at,
        row.station_id,
        row.veh_id,
        row.plate_no,
        row.predict_time_sec,
        row.predict_time_min,
        row.remain_seat_cnt,
        row.location_no,
        row.sta_order,
        row.crowded,
        row.flag,
    )


class PostgresWriter:
    def __init__(self, conn: Any) -> None:
        self._conn = conn

    def _copy(self, create_sql: str, copy_sql: str, values: Sequence[tuple[Any, ...]]) -> None:
        with self._conn.cursor() as cur:
            cur.execute(create_sql)
            with cur.copy(copy_sql) as copy:
                for value in values:
                    copy.write_row(value)

    def upsert_service_day(self, row: ServiceDayRow) -> None:
        with self._conn.cursor() as cur:
            cur.execute(
                _UPSERT_SERVICE_DAY,
                (row.service_date, row.is_weekday, row.is_holiday, row.operation_kind),
            )

    def insert_raw_polls(self, jsonl_file: str, rows: Sequence[RawPollRow]) -> RawPollInsert:
        if not rows:
            return RawPollInsert(ids={}, inserted=0)
        self._copy(_CREATE_TMP_RAW_POLL, _COPY_TMP_RAW_POLL, [_raw_poll_values(r) for r in rows])
        params = {"jsonl_file": jsonl_file}
        with self._conn.cursor() as cur:
            cur.execute(_INSERT_RAW_POLL, params)
            inserted = cur.rowcount
            cur.execute(_SELECT_RAW_POLL_IDS, params)
            ids = {int(line_no): int(raw_poll_id) for line_no, raw_poll_id in cur.fetchall()}
        return RawPollInsert(ids=ids, inserted=inserted)

    def insert_bus_positions(self, rows: Sequence[tuple[int, BusPositionRow]]) -> int:
        if not rows:
            return 0
        values = [_position_values(i, r) for i, r in rows]
        self._copy(_CREATE_TMP_BUS_POSITION, _COPY_TMP_BUS_POSITION, values)
        with self._conn.cursor() as cur:
            cur.execute(_INSERT_BUS_POSITION)
            return cur.rowcount

    def insert_bus_arrivals(self, rows: Sequence[tuple[int, BusArrivalRow]]) -> int:
        if not rows:
            return 0
        values = [_arrival_values(i, r) for i, r in rows]
        self._copy(_CREATE_TMP_BUS_ARRIVAL, _COPY_TMP_BUS_ARRIVAL, values)
        with self._conn.cursor() as cur:
            cur.execute(_INSERT_BUS_ARRIVAL)
            return cur.rowcount

    def upsert_routes(self, rows: Sequence[RouteRow]) -> int:
        values = [
            (
                r.route_id,
                r.route_name,
                r.route_type_cd,
                r.route_type_name,
                r.admin_name,
                r.region_name,
                r.start_station_name,
                r.end_station_name,
                r.is_collected,
                r.is_label_target,
                r.is_reserved,
                r.is_night,
                r.collect_interval_sec,
                r.note,
                r.source_date,
            )
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_UPSERT_ROUTE, values)
        return len(values)

    def upsert_stations(self, rows: Sequence[StationRow]) -> int:
        values = [
            (r.station_id, r.station_name, r.mobile_no, r.region_name, r.x, r.y, r.source_date)
            for r in rows
        ]
        with self._conn.cursor() as cur:
            cur.executemany(_UPSERT_STATION, values)
        return len(values)

    def upsert_route_stations(self, rows: Sequence[RouteStationRow]) -> int:
        values = [
            (r.route_id, r.station_seq, r.station_id, r.is_turn_point, r.source_date) for r in rows
        ]
        if values:
            with self._conn.cursor() as cur:
                cur.executemany(_UPSERT_ROUTE_STATION, values)
        return len(values)

    def delete_route_stations_except(self, route_id: str, keep_seqs: Sequence[int]) -> list[int]:
        if not keep_seqs:
            return []
        with self._conn.cursor() as cur:
            cur.execute(_DELETE_STALE_ROUTE_STATIONS, (route_id, list(keep_seqs)))
            return sorted(int(row[0]) for row in cur.fetchall())


def connect_kwargs(
    database_url: str,
    *,
    require_ssl: bool = True,
    sslrootcert: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """psycopg.connect 에 넘길 추가 인자.

    - sslrootcert(서버 CA 인증서 경로)가 있으면 sslmode=verify-full 로 서버 인증서와 호스트 이름까지
      확인한다.
    - 없으면 실제로 쓰일 sslmode(접속 문자열 값, 없으면 환경변수 PGSSLMODE)를 본다.
      require·verify-ca·verify-full 이면 그 값을, 없거나 약하면 require 를 sslmode 인자로
      항상 명시한다(require 는 암호화만 하고 서버 인증서는 확인하지 않는다).
    - require_ssl=False 는 SSL 없는 로컬 시험 DB 용이다(운영 CLI 는 쓰지 않는다).
    접속 문자열 해석에 실패하면 psycopg.ProgrammingError 를 올린다(메시지에 값이 들어갈 수 있어
    호출하는 쪽은 type 이름만 보여 준다).
    """
    from psycopg.conninfo import conninfo_to_dict

    kwargs: dict[str, Any] = {
        "connect_timeout": DB_CONNECT_TIMEOUT_SEC,
        "autocommit": True,
        # Supabase 풀러(트랜잭션 모드)는 서버 측 prepared statement 를 연결 간에 유지하지 않는다.
        "prepare_threshold": None,
    }
    if sslrootcert:
        kwargs["sslmode"] = "verify-full"
        kwargs["sslrootcert"] = sslrootcert
        return kwargs
    if require_ssl:
        env = os.environ if environ is None else environ
        effective = conninfo_to_dict(database_url).get("sslmode") or env.get("PGSSLMODE")
        # 판정한 값을 항상 명시한다. 비워 두면 libpq 가 서비스 파일(PGSERVICE) 등 다른 곳의
        # sslmode=disable 을 쓸 수 있다.
        kwargs["sslmode"] = effective if effective in _STRONG_SSLMODES else "require"
    return kwargs


class PostgresLoaderStore:
    """연결 하나로 날짜마다 트랜잭션을 연다(autocommit + conn.transaction())."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    @classmethod
    def connect(
        cls, database_url: str, *, require_ssl: bool = True, sslrootcert: str | None = None
    ) -> "PostgresLoaderStore":
        import psycopg

        kwargs = connect_kwargs(database_url, require_ssl=require_ssl, sslrootcert=sslrootcert)
        return cls(psycopg.connect(database_url, **kwargs))

    @contextmanager
    def transaction(self) -> Iterator[PostgresWriter]:
        with self._conn.transaction():
            yield PostgresWriter(self._conn)

    def close(self) -> None:
        self._conn.close()
