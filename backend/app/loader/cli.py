"""적재 명령. 실행: cd backend && uv run python -m app.loader <명령>

    load       --date YYYY-MM-DD | --from YYYY-MM-DD --to YYYY-MM-DD
               [--data-dir PATH] [--dry-run] [--include-trial | --exclude-trial]
               그날 raw_poll.jsonl 의 대상 줄(위치 G1300·1306, 도착 덕현초교)을 날짜마다
               한 트랜잭션으로 service_day → raw_poll → bus_position → bus_arrival 에 넣는다.
               몇 번 돌려도 결과가 같다. 시운전(mode=trial) 줄은 기본으로 넣고(mode 열로 구분,
               사례·평가 제외는 조회에서), --exclude-trial 이면 넣지 않는다.
               파일이 없는 날은 service_day(operation_kind=none)만 쓴다.
    reference  [--date YYYY-MM-DD] [--data-dir PATH] [--dry-run]
               기준정보 폴더(지정 날짜 또는 가장 최근)로 route·station·route_station 을 업서트한다.

--dry-run 은 DB 에 접속하지 않고 해석·건수만 출력한다.
DB 접속은 항상 SSL 이다. DATABASE_SSLROOTCERT(서버 CA 인증서 경로)가 있으면 verify-full,
없으면 require(접속 문자열·PGSSLMODE 가 verify-ca·verify-full 이면 그 값).
종료 코드: 0 성공, 1 사용법(인자·데이터 폴더·기준정보 폴더),
2 설정 누락(DATABASE_URL·DATABASE_SSLROOTCERT 파일·대상 ID), 3 적재 실패
(파일 읽기·DB 접속·쓰기. 실패한 날은 전부 롤백되고 그 뒤 날짜는 하지 않는다).
접속 문자열·응답 본문은 출력하지 않는다. 오류는 예외 type 이름과 SQLSTATE·제약 이름만 보여 준다.
"""

import argparse
import io
import re
import sys
import time
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path
from typing import NoReturn

from app.core.settings import Settings, get_settings
from app.loader.jsonl import DayBatch, DayStats, read_day
from app.loader.load import DayLoadResult, ReferenceLoadResult, write_day, write_reference
from app.loader.reference import ReferenceBatch, find_reference_dir, read_reference
from app.loader.rows import ARRIVAL_TARGET_NAME, LoadTargets
from app.loader.store import LoaderStore, PostgresLoaderStore

EXIT_OK = 0
EXIT_USAGE = 1
# 설정 누락: DATABASE_URL 없음, DATABASE_SSLROOTCERT 파일 없음, settings 의 대상 ID 없음.
EXIT_CONFIG_MISSING = 2
EXIT_LOAD_FAILED = 3

# 운영값(규칙 값 아님). 실수로 아주 긴 기간을 돌리지 않게 막는다.
MAX_RANGE_DAYS = 366

_DATE_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")

StoreFactory = Callable[[], LoaderStore]
Printer = Callable[[str], None]


class _UsageError(Exception):
    pass


class _ArgumentParser(argparse.ArgumentParser):
    """인자 오류의 종료 코드를 argparse 기본(2)이 아니라 1(사용법)로 한다. 2 는 DB 미설정이다."""

    def error(self, message: str) -> NoReturn:
        self.print_usage(sys.stderr)
        raise _UsageError(message)


def _configure_stdout() -> None:
    # 윈도우 콘솔 인코딩이 못 쓰는 글자는 바꿔 쓴다.
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")


def _parse_date(text: str) -> date:
    if not _DATE_PATTERN.fullmatch(text):
        raise argparse.ArgumentTypeError("형식은 YYYY-MM-DD 여야 한다")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("없는 날짜다") from exc


def _print_err(message: str) -> None:
    print(message, file=sys.stderr)


def describe_error(exc: BaseException) -> str:
    """오류 설명: type 이름, 있으면 SQLSTATE 와 제약 이름. 메시지 원문은 접속 정보·값이 들어갈 수
    있어 쓰지 않는다."""
    parts = [type(exc).__name__]
    sqlstate = getattr(exc, "sqlstate", None)
    if isinstance(sqlstate, str) and sqlstate:
        parts.append(f"sqlstate={sqlstate}")
    diag = getattr(exc, "diag", None)
    constraint = getattr(diag, "constraint_name", None) if diag is not None else None
    if isinstance(constraint, str) and constraint:
        parts.append(f"constraint={constraint}")
    return " ".join(parts)


def _n(value: int) -> str:
    return f"{value:,}"


# ---------------------------------------------------------------------------
# 요약 출력
# ---------------------------------------------------------------------------
def format_day(
    batch: DayBatch, result: DayLoadResult | None, elapsed_sec: float, *, dry_run: bool
) -> list[str]:
    stats = batch.stats
    lines = [f"== {batch.day.isoformat()} ({batch.jsonl_file}) =="]
    if not stats.file_exists:
        lines.append("수집 파일 없음: service_day(operation_kind=none)만 기록한다")
    else:
        # 날마다 같은 순서로 보이게: 노선 이름순, 도착은 맨 뒤.
        ordered = sorted(
            stats.target_lines.items(), key=lambda kv: (kv[0] == ARRIVAL_TARGET_NAME, kv[0])
        )
        targets = ", ".join(f"{name} {_n(count)}" for name, count in ordered)
        lines += [
            f"읽은 줄        {_n(stats.lines_read)}",
            f"대상 줄        {_n(stats.target_line_total)}"
            f" ({targets or '없음'}; 그중 ok=false {_n(stats.failed_target_lines)})",
            f"건너뛴 줄      {_n(stats.skipped_lines)}"
            f" (다른 노선·정류장·API {_n(stats.other_target_lines)},"
            f" 깨진 줄 {_n(stats.broken_lines)}, 빈 줄 {_n(stats.blank_lines)},"
            f" 쓰는 중이던 마지막 줄 {_n(stats.partial_last_lines)},"
            f" mode 이상 {_n(stats.invalid_mode_lines)},"
            f" 시운전 제외 {_n(stats.trial_lines_excluded)})",
        ]
        if stats.partial_last_lines:
            lines.append("  (쓰는 중이던 마지막 줄은 다음 실행에서 같은 줄 번호로 들어간다)")
        if result is None:
            lines.append(f"raw_poll       넣을 줄 {_n(stats.target_line_total)} (DB 확인 안 함)")
        else:
            lines.append(
                f"raw_poll       새로 {_n(result.raw_poll_inserted)},"
                f" 이미 있음 {_n(result.raw_poll_existing)}"
            )
        position_new = "" if result is None else f", 새로 {_n(result.positions_inserted)}"
        arrival_new = "" if result is None else f", 새로 {_n(result.arrivals_inserted)}"
        lines += [
            f"bus_position   행 {_n(stats.position_rows)}{position_new}",
            f"bus_arrival    행 {_n(stats.arrival_rows)}{arrival_new}"
            f" (차 정보 없는 순위 {_n(stats.arrival_empty_ranks)},"
            f" 대상 밖 노선 항목 {_n(stats.arrival_other_route_items)})",
        ]
    service_day = batch.service_day
    lines += [
        f"service_day    평일 {'예' if service_day.is_weekday else '아니오'},"
        f" 공휴일 {'예' if service_day.is_holiday else '아니오'},"
        f" 운영 {service_day.operation_kind}",
        f"소요           {elapsed_sec:.2f}초" + (" (dry-run, DB 쓰지 않음)" if dry_run else ""),
    ]
    return lines


def format_totals(batches: Sequence[tuple[DayStats, DayLoadResult | None]]) -> list[str]:
    read = sum(s.lines_read for s, _ in batches)
    target = sum(s.target_line_total for s, _ in batches)
    skipped = sum(s.skipped_lines for s, _ in batches)
    positions = sum(s.position_rows for s, _ in batches)
    arrivals = sum(s.arrival_rows for s, _ in batches)
    lines = [
        f"== 합계 {len(batches)}일 ==",
        f"읽은 줄 {_n(read)}, 대상 줄 {_n(target)}, 건너뛴 줄 {_n(skipped)},"
        f" bus_position 행 {_n(positions)}, bus_arrival 행 {_n(arrivals)}",
    ]
    results = [r for _, r in batches if r is not None]
    if results:
        lines.append(
            f"raw_poll 새로 {_n(sum(r.raw_poll_inserted for r in results))},"
            f" 이미 있음 {_n(sum(r.raw_poll_existing for r in results))},"
            f" bus_position 새로 {_n(sum(r.positions_inserted for r in results))},"
            f" bus_arrival 새로 {_n(sum(r.arrivals_inserted for r in results))}"
        )
    return lines


def seq_ranges(seqs: Sequence[int]) -> str:
    """순번 목록을 '1-3, 7' 처럼 줄여 쓴다."""
    ordered = sorted(set(seqs))
    parts: list[str] = []
    start = prev = None
    for seq in ordered:
        if start is None:
            start = prev = seq
        elif seq == prev + 1:
            prev = seq
        else:
            parts.append(f"{start}" if start == prev else f"{start}-{prev}")
            start = prev = seq
    if start is not None:
        parts.append(f"{start}" if start == prev else f"{start}-{prev}")
    return ", ".join(parts) or "없음"


def format_route_station_plan(
    batch: ReferenceBatch, result: ReferenceLoadResult | None
) -> list[str]:
    """노선별 순번 정리 계획(dry-run) 또는 결과. dry-run 은 DB 를 보지 않으므로 지울 순번 대신
    '이 순번만 남긴다'를 보여 준다(그 밖의 DB 순번이 지워진다)."""
    names = {r.route_id: r.route_name for r in batch.routes}
    lines: list[str] = []
    for route_id, rows in sorted(batch.route_station_groups().items()):
        label = f"  {names.get(route_id, route_id)}({route_id})"
        keep = seq_ranges([r.station_seq for r in rows])
        skipped = batch.stats.skipped_items_by_route.get(route_id, 0)
        if not batch.can_trim(route_id):
            lines.append(f"{label}: 순번 {keep} 업서트, 건너뛴 항목 {skipped}개라 지우지 않음")
        elif result is None:
            lines.append(f"{label}: 순번 {keep} 만 남김(그 밖의 DB 순번을 지움, DB 확인 안 함)")
        else:
            removed = result.deleted_seqs.get(route_id, [])
            lines.append(f"{label}: 순번 {keep} 업서트, 지운 순번 {seq_ranges(removed)}")
    return lines


def trim_warnings(batch: ReferenceBatch) -> list[str]:
    names = {r.route_id: r.route_name for r in batch.routes}
    return [
        f"경고: {names.get(route_id, route_id)} 정류장 목록에 건너뛴 항목"
        f" {batch.stats.skipped_items_by_route.get(route_id, 0)}개가 있어"
        " 기준정보에서 없어진 순번을 지우지 않는다"
        for route_id in sorted(batch.route_station_groups())
        if not batch.can_trim(route_id)
    ]


def format_reference(batch: ReferenceBatch, data_dir: Path, *, dry_run: bool) -> list[str]:
    stats = batch.stats
    try:
        folder = batch.folder.relative_to(data_dir).as_posix()
    except ValueError:
        folder = batch.folder.name
    return [
        f"== 기준정보 {folder} ==",
        f"기록 파일      {stats.record_files}"
        f" (노선 검색 {stats.route_list_records}, 정류장 목록 {stats.route_station_records},"
        f" 못 쓴 기록 {stats.unusable_records},"
        f" settings 에 없는 노선 {stats.unknown_route_records})",
        f"route          {len(batch.routes)}"
        f" (검색 기록으로 유형·관할을 채운 노선: {', '.join(stats.routes_with_details) or '없음'})",
        f"station        {len(batch.stations)}",
        f"route_station  {len(batch.route_stations)}"
        f" (건너뛴 정류장 항목 {stats.skipped_station_items})"
        + (" (dry-run, DB 쓰지 않음)" if dry_run else ""),
    ]


# ---------------------------------------------------------------------------
# 명령
# ---------------------------------------------------------------------------
def _days(start: date, end: date) -> list[date]:
    return [start + timedelta(days=i) for i in range((end - start).days + 1)]


def _open_store(store_factory: StoreFactory, err: Printer) -> LoaderStore | None:
    try:
        return store_factory()
    except Exception as exc:  # 경계: 접속 실패를 종료 코드 3 으로 바꾼다(메시지 원문은 쓰지 않음)
        err(f"DB 접속 실패: {describe_error(exc)}")
        return None


def cmd_load(
    *,
    days: Sequence[date],
    data_dir: Path,
    include_trial: bool,
    targets: LoadTargets,
    store_factory: StoreFactory | None,
    out: Printer,
    err: Printer,
) -> int:
    """store_factory 가 None 이면 dry-run(DB 없이 해석·건수만)."""
    dry_run = store_factory is None
    store: LoaderStore | None = None
    if store_factory is not None:
        store = _open_store(store_factory, err)
        if store is None:
            return EXIT_LOAD_FAILED
    done: list[tuple[DayStats, DayLoadResult | None]] = []
    try:
        for day in days:
            started = time.perf_counter()
            try:
                batch = read_day(data_dir, day, include_trial=include_trial, targets=targets)
            except OSError as exc:
                err(f"[{day.isoformat()}] 파일 읽기 실패: {describe_error(exc)}")
                return EXIT_LOAD_FAILED
            result: DayLoadResult | None = None
            if store is not None:
                try:
                    result = write_day(store, batch)
                except Exception as exc:  # 경계: 그날은 롤백됐다. 종료 코드 3 으로 바꾼다
                    err(f"[{day.isoformat()}] 적재 실패(그날 전체 롤백): {describe_error(exc)}")
                    return EXIT_LOAD_FAILED
            for line in format_day(batch, result, time.perf_counter() - started, dry_run=dry_run):
                out(line)
            done.append((batch.stats, result))
    finally:
        if store is not None:
            store.close()
    if len(done) > 1:
        for line in format_totals(done):
            out(line)
    return EXIT_OK


def cmd_reference(
    *,
    day: date | None,
    data_dir: Path,
    store_factory: StoreFactory | None,
    out: Printer,
    err: Printer,
) -> int:
    """store_factory 가 None 이면 dry-run."""
    folder = find_reference_dir(data_dir, day)
    if folder is None:
        err("기준정보 폴더가 없다" + (f": {day.isoformat()}" if day else ""))
        return EXIT_USAGE
    try:
        batch = read_reference(folder, date.fromisoformat(folder.name))
    except OSError as exc:
        err(f"기준정보 읽기 실패: {describe_error(exc)}")
        return EXIT_LOAD_FAILED
    lines = format_reference(batch, data_dir, dry_run=store_factory is None)
    for warning in trim_warnings(batch):
        err(warning)
    if store_factory is None:
        lines += format_route_station_plan(batch, None)
    else:
        store = _open_store(store_factory, err)
        if store is None:
            return EXIT_LOAD_FAILED
        try:
            result = write_reference(store, batch)
        except Exception as exc:  # 경계: 전체 롤백됐다. 종료 코드 3 으로 바꾼다
            err(f"기준정보 적재 실패(전체 롤백): {describe_error(exc)}")
            return EXIT_LOAD_FAILED
        finally:
            store.close()
        lines.append(f"지운 순번      {result.route_stations_deleted} (기준정보에서 없어진 순번)")
        lines += format_route_station_plan(batch, result)
    for line in lines:
        out(line)
    return EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(
        prog="python -m app.loader", description="수집 JSONL·기준정보를 DB 에 적재"
    )
    sub = parser.add_subparsers(dest="command", required=True, parser_class=_ArgumentParser)

    def add_common(p: argparse.ArgumentParser) -> None:
        p.add_argument(
            "--data-dir", type=Path, metavar="PATH", help="수집 데이터 폴더(기본: 설정의 data_dir)"
        )
        p.add_argument("--dry-run", action="store_true", help="DB 없이 해석·건수만 출력")

    load = sub.add_parser("load", help="그날 raw_poll.jsonl 을 DB 에 적재")
    day_group = load.add_mutually_exclusive_group(required=True)
    day_group.add_argument("--date", type=_parse_date, metavar="YYYY-MM-DD")
    day_group.add_argument("--from", dest="date_from", type=_parse_date, metavar="YYYY-MM-DD")
    load.add_argument("--to", dest="date_to", type=_parse_date, metavar="YYYY-MM-DD")
    trial_group = load.add_mutually_exclusive_group()
    trial_group.add_argument(
        "--include-trial",
        dest="include_trial",
        action="store_true",
        default=True,
        help="시운전(mode=trial) 줄도 넣는다(기본)",
    )
    trial_group.add_argument(
        "--exclude-trial",
        dest="include_trial",
        action="store_false",
        help="시운전(mode=trial) 줄을 넣지 않는다",
    )
    add_common(load)

    reference = sub.add_parser("reference", help="기준정보로 route·station·route_station 업서트")
    reference.add_argument(
        "--date", type=_parse_date, metavar="YYYY-MM-DD", help="기준정보 날짜(기본: 가장 최근)"
    )
    add_common(reference)
    return parser


def _load_days(args: argparse.Namespace) -> list[date]:
    if args.date is not None:
        if args.date_to is not None:
            raise _UsageError("--to 는 --from 과 함께 쓴다")
        return [args.date]
    if args.date_to is None:
        raise _UsageError("--from 에는 --to 가 필요하다")
    if args.date_to < args.date_from:
        raise _UsageError("--to 가 --from 보다 앞이다")
    if (args.date_to - args.date_from).days + 1 > MAX_RANGE_DAYS:
        raise _UsageError(f"기간은 {MAX_RANGE_DAYS}일 이하여야 한다")
    return _days(args.date_from, args.date_to)


def _postgres_factory(settings: Settings, sslrootcert: Path | None) -> StoreFactory:
    def factory() -> LoaderStore:
        return PostgresLoaderStore.connect(
            settings.database_url.get_secret_value().strip(),
            sslrootcert=str(sslrootcert) if sslrootcert is not None else None,
        )

    return factory


def main(
    argv: Sequence[str] | None = None,
    *,
    settings: Settings | None = None,
    store_factory: StoreFactory | None = None,
    out: Printer = print,
    err: Printer = _print_err,
) -> int:
    """store_factory 를 주면(테스트) 그것을 쓰고, 아니면 settings 의 DATABASE_URL 로 접속한다."""
    _configure_stdout()
    try:
        args = build_parser().parse_args(argv)
        days = _load_days(args) if args.command == "load" else []
    except _UsageError as exc:
        err(f"사용법 오류: {exc}")
        return EXIT_USAGE

    resolved = settings if settings is not None else get_settings()
    data_dir: Path = args.data_dir if args.data_dir is not None else resolved.data_dir
    if not data_dir.is_dir():
        err(f"데이터 폴더가 없다: {data_dir}")
        return EXIT_USAGE

    if args.dry_run:
        store_factory = None
    elif store_factory is None:
        if not resolved.has_database_url:
            err("DATABASE_URL 이 설정되지 않았다. --dry-run 은 DB 없이 돈다")
            return EXIT_CONFIG_MISSING
        sslrootcert = resolved.database_sslrootcert_path
        if sslrootcert is not None and not sslrootcert.is_file():
            err(f"DATABASE_SSLROOTCERT 파일이 없다: {sslrootcert}")
            return EXIT_CONFIG_MISSING
        store_factory = _postgres_factory(resolved, sslrootcert)

    if args.command == "load":
        try:
            targets = LoadTargets.from_settings()
        except ValueError as exc:
            err(f"설정 누락: {exc}")
            return EXIT_CONFIG_MISSING
        return cmd_load(
            days=days,
            data_dir=data_dir,
            include_trial=args.include_trial,
            targets=targets,
            store_factory=store_factory,
            out=out,
            err=err,
        )
    if args.command == "reference":
        return cmd_reference(
            day=args.date, data_dir=data_dir, store_factory=store_factory, out=out, err=err
        )
    raise AssertionError(f"알 수 없는 명령: {args.command}")
