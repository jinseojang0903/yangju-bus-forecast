"""PostgreSQL 저장소: 접속 인자(SSL 강제) 단위 시험과, 선택 통합 시험.

통합 시험은 환경변수 LOADER_TEST_DATABASE_URL 이 있을 때만 돈다(기본 skip).
- 0001_init.sql 을 적용한 **시험용** DB 를 가리킨다. 운영 Supabase 에는 돌리지 않는다.
- 운영 데이터와 겹치지 않게 픽스처를 먼 미래 날짜 폴더(2099-01-05)로 복사해 넣고, 끝나면 그 날짜의
  raw_poll·bus_position·bus_arrival·service_day 행만 지운다.
- SSL 없는 로컬 DB 면 LOADER_TEST_REQUIRE_SSL=0 을 함께 준다.
- 실수로 운영 DB 에 돌지 않게 두 가지를 확인하고 아니면 건너뛴다.
  1) LOADER_TEST_DATABASE_URL 이 설정(Settings)의 DATABASE_URL 과 같으면 건너뛴다
     (비교만 하고 값은 출력하지 않는다).
  2) 접속한 DB 에 표시 테이블 public._loader_test_marker 가 있어야 한다. 시험 DB 를 만들 때 한 번
     `CREATE TABLE public._loader_test_marker ();` 를 실행해 둔다(운영 DB 에는 만들지 않는다).
"""

import os
from datetime import date
from pathlib import Path

import pytest

from app.core.settings import Settings
from app.loader import cli
from app.loader.store import DB_CONNECT_TIMEOUT_SEC, PostgresLoaderStore, connect_kwargs
from tests.loader.fakes import FIXTURE_DATA_DIR, FIXTURE_DAY

INTEGRATION_URL_ENV = "LOADER_TEST_DATABASE_URL"
INTEGRATION_DAY = date(2099, 1, 5)


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("postgresql://u@h/db", "require"),
        ("postgresql://u@h/db?sslmode=disable", "require"),
        ("postgresql://u@h/db?sslmode=prefer", "require"),
        ("postgresql://u@h/db?sslmode=verify-full", "verify-full"),
        ("postgresql://u@h/db?sslmode=verify-ca", "verify-ca"),
        ("host=h dbname=db sslmode=require", "require"),
        # 서비스 파일(PGSERVICE)의 sslmode=disable 이 이기지 않게 항상 명시한다.
        ("service=yangju", "require"),
    ],
)
def test_connect_kwargs_forces_ssl(url: str, expected: str) -> None:
    kwargs = connect_kwargs(url, environ={})
    assert kwargs.get("sslmode") == expected
    assert kwargs["connect_timeout"] == DB_CONNECT_TIMEOUT_SEC
    assert kwargs["autocommit"] is True


def test_connect_kwargs_without_ssl_for_local_tests() -> None:
    assert "sslmode" not in connect_kwargs("postgresql://u@localhost/db", require_ssl=False)


@pytest.mark.parametrize(
    ("url", "pgsslmode", "expected"),
    [
        ("postgresql://u@h/db", "verify-full", "verify-full"),  # 환경변수가 더 강하면 그 값
        ("postgresql://u@h/db", "verify-ca", "verify-ca"),
        ("postgresql://u@h/db", "require", "require"),
        ("postgresql://u@h/db", "prefer", "require"),
        ("postgresql://u@h/db", "disable", "require"),
        # 접속 문자열 값이 환경변수보다 우선이므로, 문자열이 약하면 환경변수가 강해도 require.
        ("postgresql://u@h/db?sslmode=disable", "verify-full", "require"),
    ],
)
def test_connect_kwargs_reads_pgsslmode(url: str, pgsslmode: str, expected: str) -> None:
    assert connect_kwargs(url, environ={"PGSSLMODE": pgsslmode}).get("sslmode") == expected


def test_connect_kwargs_with_root_cert_verifies_server() -> None:
    kwargs = connect_kwargs(
        "postgresql://u@h/db?sslmode=require", sslrootcert="/etc/ssl/supabase-ca.crt", environ={}
    )
    assert kwargs["sslmode"] == "verify-full"
    assert kwargs["sslrootcert"] == "/etc/ssl/supabase-ca.crt"


MARKER_TABLE = "public._loader_test_marker"


@pytest.fixture
def integration_url() -> str:
    url = os.environ.get(INTEGRATION_URL_ENV, "").strip()
    if not url:
        pytest.skip(f"{INTEGRATION_URL_ENV} 가 없어 통합 시험을 건너뛴다")
    configured = Settings().database_url.get_secret_value().strip()
    if configured and configured == url:
        pytest.skip(f"{INTEGRATION_URL_ENV} 가 운영 DATABASE_URL 과 같아 건너뛴다")
    import psycopg

    require_ssl = os.environ.get("LOADER_TEST_REQUIRE_SSL", "1") != "0"
    with psycopg.connect(url, **connect_kwargs(url, require_ssl=require_ssl)) as conn:
        row = conn.execute("SELECT to_regclass(%s) IS NOT NULL", (MARKER_TABLE,)).fetchone()
    if row is None or row[0] is not True:
        pytest.skip(f"시험 DB 표시 테이블 {MARKER_TABLE} 이 없어 건너뛴다(시험 DB 에만 만든다)")
    return url


def _cleanup(url: str, require_ssl: bool) -> None:
    import psycopg

    jsonl_file = f"{INTEGRATION_DAY.isoformat()}/raw_poll.jsonl"
    with psycopg.connect(url, **connect_kwargs(url, require_ssl=require_ssl)) as conn:
        with conn.transaction():
            conn.execute(
                "DELETE FROM public.bus_position WHERE raw_poll_id IN"
                " (SELECT raw_poll_id FROM public.raw_poll WHERE jsonl_file = %s)",
                (jsonl_file,),
            )
            conn.execute(
                "DELETE FROM public.bus_arrival WHERE raw_poll_id IN"
                " (SELECT raw_poll_id FROM public.raw_poll WHERE jsonl_file = %s)",
                (jsonl_file,),
            )
            conn.execute("DELETE FROM public.raw_poll WHERE jsonl_file = %s", (jsonl_file,))
            conn.execute(
                "DELETE FROM public.service_day WHERE service_date = %s", (INTEGRATION_DAY,)
            )


def _counts(url: str, require_ssl: bool) -> tuple[int, int, int]:
    import psycopg

    jsonl_file = f"{INTEGRATION_DAY.isoformat()}/raw_poll.jsonl"
    with psycopg.connect(url, **connect_kwargs(url, require_ssl=require_ssl)) as conn:
        row = conn.execute(
            """
            SELECT
              (SELECT count(*) FROM public.raw_poll WHERE jsonl_file = %(f)s),
              (SELECT count(*) FROM public.bus_position p
                 JOIN public.raw_poll r USING (raw_poll_id) WHERE r.jsonl_file = %(f)s),
              (SELECT count(*) FROM public.bus_arrival a
                 JOIN public.raw_poll r USING (raw_poll_id) WHERE r.jsonl_file = %(f)s)
            """,
            {"f": jsonl_file},
        ).fetchone()
    assert row is not None
    return int(row[0]), int(row[1]), int(row[2])


def test_postgres_load_is_idempotent(integration_url: str, tmp_path: Path) -> None:
    require_ssl = os.environ.get("LOADER_TEST_REQUIRE_SSL", "1") != "0"
    day_dir = tmp_path / INTEGRATION_DAY.isoformat()
    day_dir.mkdir()
    source = FIXTURE_DATA_DIR / FIXTURE_DAY.isoformat() / "raw_poll.jsonl"
    (day_dir / "raw_poll.jsonl").write_bytes(source.read_bytes())

    def factory() -> PostgresLoaderStore:
        return PostgresLoaderStore.connect(integration_url, require_ssl=require_ssl)

    argv = ["load", "--date", INTEGRATION_DAY.isoformat(), "--data-dir", str(tmp_path)]
    settings = Settings(_env_file=None, database_url="")
    _cleanup(integration_url, require_ssl)
    try:
        assert cli.main(argv, settings=settings, store_factory=factory) == cli.EXIT_OK
        assert _counts(integration_url, require_ssl) == (8, 5, 3)
        assert cli.main(argv, settings=settings, store_factory=factory) == cli.EXIT_OK
        assert _counts(integration_url, require_ssl) == (8, 5, 3)
    finally:
        _cleanup(integration_url, require_ssl)
