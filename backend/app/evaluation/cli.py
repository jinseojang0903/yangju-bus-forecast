"""백테스트 명령. 실행: cd backend && uv run python -m app.evaluation <명령>

    backtest  --from YYYY-MM-DD --to YYYY-MM-DD [--data-dir PATH] [--strict-only]
              [--out PATH [--force]]
              기간 안 날짜별 raw_poll.jsonl 의 위치 기록으로 운행편·정답·사례 특징을 만들고,
              평가 대상 운행편 × 선행시간마다 단순 기준(방법 A)과 사례 방식(방법 B)의 판단을 내
              선행시간별·노선별 비교 표(마크다운)를 출력한다.
              --strict-only 는 정답 등급 strict 만 쓴다(사례와 평가 대상 모두).
              기본은 strict+relaxed.
              --out 은 운행편 × 선행시간 행을 CSV 로 저장한다. CSV 는 수집 데이터에서 만든
              파생물이므로 저장소 밖이나 data/backtest/ 에 저장하고 커밋하지 않는다.
              이미 있는 파일은 --force 가 없으면 덮어쓰지 않는다. 열 설명은 report.write_rows_csv.

주말은 수집 요일이 아니므로 읽지 않는다. 평일인데 파일이 없는 날은 건너뛰고 출력에 적는다.
시운전(mode=trial) 기록도 읽는다. 앞차 간격에는 쓰고 사례·평가 대상에서는 뺀다.
입력 파일은 읽기만 한다. DB 는 쓰지 않는다.
출력의 경로는 저장소 안이면 저장소 기준 상대 경로, 밖이면 이름만 쓴다(결과 표를 PR 등에
붙일 때 로컬 사용자 경로가 드러나지 않게).
종료 코드: 0 정상, 1 목표 순번이 위치 기록과 다른 날이 있음(표는 출력한다),
2 기간 안에 읽을 파일이 없음·기간 오류·설정 누락·출력 파일이 이미 있음·파일을 읽거나 쓰지 못함.
"""

import argparse
import io
import re
import sys
from collections.abc import Callable, Sequence
from datetime import date, timedelta
from pathlib import Path

from app.collector.storage import raw_poll_path
from app.core.settings import (
    COLLECT_WINDOW,
    FORECAST_RULES,
    PUBLISH_CRITERIA,
    SERVICE_HOURS,
    get_settings,
)
from app.evaluation.backtest import DEFAULT_GRADES, LabeledTrip, evaluate_labeled_trips, label_trips
from app.evaluation.metrics import summarize_methods, summarize_sample
from app.evaluation.report import RunInfo, display_path, format_report, write_rows_csv
from app.forecast.service_time import is_service_day
from app.labeling.labels import Grade
from app.labeling.records import ReadStats, load_raw_poll
from app.labeling.targets import check_target_seqs, resolve_target_stops
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


def _days_between(date_from: date, date_to: date) -> list[date]:
    return [date_from + timedelta(days=n) for n in range((date_to - date_from).days + 1)]


def cmd_backtest(
    *,
    date_from: date,
    date_to: date,
    data_dir: Path,
    strict_only: bool,
    out_path: Path | None,
    force: bool = False,
    out: Callable[[str], None] = print,
) -> int:
    if date_from > date_to:
        out(
            f"기간이 잘못됐다: --from {date_from.isoformat()} 이"
            f" --to {date_to.isoformat()} 보다 늦다"
        )
        return EXIT_INPUT_MISSING
    # 계산 전에 확인해, 오래 돌린 뒤 저장 단계에서 멈추지 않게 한다.
    if out_path is not None and out_path.exists() and not force:
        out(f"출력 파일이 이미 있다: {display_path(out_path)} (덮어쓰려면 --force)")
        return EXIT_INPUT_MISSING
    try:
        stops = resolve_target_stops()
    except ValueError as exc:
        out(f"설정 누락: {exc}")
        return EXIT_INPUT_MISSING
    route_names = {route_id: stop.route_name for route_id, stop in stops.items()}
    grades = (Grade.STRICT,) if strict_only else DEFAULT_GRADES

    read_days: list[date] = []
    missing_days: list[date] = []
    weekend_days: list[date] = []
    weekend_days_with_file: list[date] = []
    non_service_days: list[date] = []
    seq_mismatch_days: list[date] = []
    total = ReadStats()
    trial_records = 0
    labeled_trips: list[LabeledTrip] = []

    for day in _days_between(date_from, date_to):
        source = raw_poll_path(data_dir, day)
        if day.weekday() not in COLLECT_WINDOW.weekdays:
            weekend_days.append(day)
            if source.is_file():
                weekend_days_with_file.append(day)
            continue
        if not source.is_file():
            missing_days.append(day)
            continue
        # 시운전 기록도 읽는다. 시운전 운행편도 실제로 앞서 달린 차량이라 앞차 간격에 필요하다.
        records, stats = load_raw_poll(source, include_trial=True)
        read_days.append(day)
        total.merge(stats)

        target_records = [r for r in records if r.route_id in stops]
        trial_records += sum(1 for r in target_records if r.is_trial)
        if not is_service_day(day) or any(r.is_holiday for r in target_records):
            non_service_days.append(day)
        if any(c.is_observed_mismatch for c in check_target_seqs(target_records, stops, {})):
            seq_mismatch_days.append(day)
        # 하루치씩 라벨과 특징을 만들고 위치 기록은 버린다(앞차 간격은 같은 날 안에서만 본다).
        labeled_trips.extend(label_trips(build_trips(target_records), stops))

    if not read_days:
        out(
            f"기간 안에 읽을 수집 파일이 없다: {date_from.isoformat()} ~ {date_to.isoformat()}"
            f" ({display_path(data_dir)})"
        )
        missing_text = ", ".join(day.isoformat() for day in missing_days) or "없음"
        out(f"건너뛴 날짜(평일인데 수집 파일 없음) {len(missing_days)}일: {missing_text}")
        return EXIT_INPUT_MISSING

    result = evaluate_labeled_trips(labeled_trips, grades=grades)
    info = RunInfo(
        date_from=date_from,
        date_to=date_to,
        data_dir=data_dir,
        grades=result.grades,
        read_days=read_days,
        missing_days=missing_days,
        weekend_days=weekend_days,
        weekend_days_with_file=weekend_days_with_file,
        non_service_days=non_service_days,
        seq_mismatch_days=seq_mismatch_days,
        lines=total.lines,
        location_lines=total.location_lines,
        failed_location_lines=total.failed_location_lines,
        broken_lines=total.broken_lines,
        trial_records=trial_records,
        items=total.items,
        skipped_items=total.skipped_items,
    )
    for line in format_report(
        info=info,
        samples=summarize_sample(result.selections, route_names),
        methods=summarize_methods(result.rows, route_names, result.lead_times_min),
        rules=FORECAST_RULES,
        criteria=PUBLISH_CRITERIA,
        hours=SERVICE_HOURS,
    ):
        out(line)

    if out_path is not None:
        row_count = write_rows_csv(out_path, result.rows, route_names)
        out("")
        out(f"CSV 저장: {display_path(out_path)} ({row_count}행)")
    return EXIT_TARGET_MISMATCH if seq_mismatch_days else EXIT_OK


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m app.evaluation", description="백테스트: 단순 기준과 사례 방식 비교"
    )
    sub = parser.add_subparsers(dest="command", required=True)
    backtest = sub.add_parser("backtest", help="기간 안 수집 기록으로 두 방법을 비교한 표를 출력")
    backtest.add_argument(
        "--from", dest="date_from", required=True, type=_parse_date, metavar="YYYY-MM-DD"
    )
    backtest.add_argument(
        "--to", dest="date_to", required=True, type=_parse_date, metavar="YYYY-MM-DD"
    )
    backtest.add_argument(
        "--data-dir", type=Path, metavar="PATH", help="수집 데이터 폴더(기본: 설정의 data_dir)"
    )
    backtest.add_argument(
        "--strict-only", action="store_true", help="정답 등급 strict 만 쓴다(사례·평가 대상 모두)"
    )
    backtest.add_argument(
        "--out",
        type=Path,
        metavar="PATH",
        help=(
            "운행편 × 선행시간 행 CSV 저장 경로. 수집 파생물이므로 저장소 밖이나 data/backtest/ 에"
            " 저장하고 커밋하지 않는다. status 가 missing_input 이면 n·k 는 0 이며 계산하지"
            " 않았다는 뜻이므로 status 와 함께 본다"
        ),
    )
    backtest.add_argument("--force", action="store_true", help="--out 파일이 이미 있으면 덮어쓴다")
    return parser


def _file_error_message(exc: OSError | UnicodeDecodeError) -> str:
    """파일 오류를 한 줄로. 경로 전체·스택은 내지 않고 파일 이름만 쓴다."""
    if isinstance(exc, UnicodeDecodeError):
        return "수집 파일을 UTF-8 로 읽지 못했다(UnicodeDecodeError)"
    name = Path(exc.filename).name if exc.filename else "-"
    return f"파일을 읽거나 쓰지 못했다: {name} ({type(exc).__name__})"


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    _configure_stdout()
    if args.command == "backtest":
        data_dir = args.data_dir if args.data_dir is not None else get_settings().data_dir
        try:
            return cmd_backtest(
                date_from=args.date_from,
                date_to=args.date_to,
                data_dir=data_dir,
                strict_only=args.strict_only,
                out_path=args.out,
                force=args.force,
            )
        # CLI 경계에서 파일 오류만 한 줄 메시지로 바꾼다. 그 밖의 예외는 버그이므로 그대로 올린다.
        except (OSError, UnicodeDecodeError) as exc:
            print(_file_error_message(exc))
            return EXIT_INPUT_MISSING
    raise AssertionError(f"알 수 없는 명령: {args.command}")
