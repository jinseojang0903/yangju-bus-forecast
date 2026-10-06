"""운행편·라벨 명령. 실행: cd backend && uv run python -m app.labeling <명령>

    report  --date YYYY-MM-DD [--data-dir PATH] [--include-trial] [--csv PATH]
            그날 raw_poll.jsonl 의 위치 기록으로 운행편을 만들고 목표 정류장(덕현초교 잠실행)
            기준 등급·라벨을 붙여 노선별·등급별·미확인 사유별 표를 출력한다.
            기본은 시운전(mode=trial) 기록을 빼고, --include-trial 이면 넣는다.
            --csv 는 운행편별 라벨을 CSV 로 저장한다.

입력 파일은 읽기만 한다. 종료 코드: 0 정상, 1 목표 순번이 근거(위치 기록·targets.json)와 다름,
2 입력 파일 없음 또는 설정 누락.
"""

import argparse
import io
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date
from pathlib import Path

from app.collector.storage import raw_poll_path
from app.core.settings import COLLECT_TARGET, get_settings
from app.labeling.constants import TRIP_SPLIT_GAP_SEC, TRIP_SPLIT_SEQ_DROP
from app.labeling.labels import label_trip
from app.labeling.records import load_raw_poll
from app.labeling.report import format_report, summarize, write_labels_csv
from app.labeling.targets import (
    check_target_seqs,
    find_targets_file,
    load_reference_seqs,
    resolve_target_stops,
)
from app.labeling.trips import build_trips

EXIT_OK = 0
EXIT_TARGET_MISMATCH = 1
EXIT_INPUT_MISSING = 2

_DATE_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}")


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


def cmd_report(
    *,
    day: date,
    data_dir: Path,
    include_trial: bool,
    csv_path: Path | None,
    out: Callable[[str], None] = print,
) -> int:
    source = raw_poll_path(data_dir, day)
    if not source.is_file():
        out(f"수집 파일이 없다: {source}")
        return EXIT_INPUT_MISSING
    try:
        stops = resolve_target_stops()
    except ValueError as exc:
        out(f"설정 누락: {exc}")
        return EXIT_INPUT_MISSING
    # resolve_target_stops 가 board_station_id 가 있는지 확인했다.
    target_station_id = COLLECT_TARGET.board_station_id or ""

    records, stats = load_raw_poll(source, include_trial=include_trial)

    targets_file = find_targets_file(data_dir, day)
    targets_file_error: str | None = None
    reference_seqs: dict[str, int] = {}
    if targets_file is not None:
        try:
            reference_seqs = load_reference_seqs(targets_file, target_station_id)
        except (OSError, ValueError) as exc:
            # 확인용 보조 근거라 리포트는 계속 낸다. 실패 사실은 리포트에 적는다.
            targets_file_error = type(exc).__name__
    checks = check_target_seqs(records, stops, reference_seqs)

    trips = build_trips(records)
    labels = [label_trip(t, stops[t.route_id].station_seq) for t in trips if t.route_id in stops]
    route_names = {route_id: stop.route_name for route_id, stop in stops.items()}

    for line in format_report(
        day=day,
        source=source,
        include_trial=include_trial,
        stats=stats,
        target_station_id=target_station_id,
        checks=checks,
        targets_file=targets_file,
        targets_file_error=targets_file_error,
        summaries=summarize(labels, route_names),
        unlabeled_trips=len(trips) - len(labels),
        gap_sec=TRIP_SPLIT_GAP_SEC,
        seq_drop=TRIP_SPLIT_SEQ_DROP,
    ):
        out(line)

    if csv_path is not None:
        rows = write_labels_csv(csv_path, labels, route_names)
        out("")
        out(f"CSV 저장: {csv_path} ({rows}행)")
    return EXIT_TARGET_MISMATCH if any(c.is_mismatch for c in checks) else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.labeling", description="운행편 재구성과 정답 라벨(F06)"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    report = sub.add_parser("report", help="그날 운행편 라벨을 노선·등급·사유별로 집계해 출력")
    report.add_argument("--date", required=True, type=_parse_date, metavar="YYYY-MM-DD")
    report.add_argument(
        "--data-dir", type=Path, metavar="PATH", help="수집 데이터 폴더(기본: 설정의 data_dir)"
    )
    report.add_argument(
        "--include-trial", action="store_true", help="시운전(mode=trial) 기록도 넣는다"
    )
    report.add_argument("--csv", type=Path, metavar="PATH", help="운행편별 라벨 CSV 저장 경로")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_stdout()
    if args.command == "report":
        data_dir = args.data_dir if args.data_dir is not None else get_settings().data_dir
        return cmd_report(
            day=args.date,
            data_dir=data_dir,
            include_trial=args.include_trial,
            csv_path=args.csv,
        )
    raise AssertionError(f"알 수 없는 명령: {args.command}")
