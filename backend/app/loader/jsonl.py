"""하루치 raw_poll.jsonl 을 한 줄씩 읽어 적재할 행과 집계를 만든다(DB 없이).

- 줄 번호는 물리적 줄 번호다(1부터, 빈 줄·깨진 줄 포함). raw_poll(jsonl_file, line_no) 키가
  파일을 다시 읽어도 같은 줄을 가리키게 하려는 것이다(수집기는 덧붙이기만 한다).
- 줄바꿈 없이 끝나는 마지막 줄은 수집기가 쓰는 중일 수 있어 넣지 않고 센다. 다음 실행에서
  줄이 완성되면 같은 줄 번호로 들어간다.
- 조용히 버리는 줄이 없게 건너뛴 이유를 모두 센다.
"""

import json
from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from app.collector.collector import MODE_TRIAL
from app.collector.storage import raw_poll_path
from app.core.settings import RAW_POLL_FILENAME
from app.loader.rows import (
    LoadTargets,
    ParsedLine,
    ServiceDayRow,
    build_line,
    build_service_day,
    jsonl_file_name,
    line_mode,
)


@dataclass
class DayStats:
    """하루 파일 읽기 집계. 요약에 그대로 보여 준다."""

    file_exists: bool = True
    lines_read: int = 0  # 물리적 줄 수(쓰는 중이던 마지막 줄 포함)
    blank_lines: int = 0
    broken_lines: int = 0  # JSON 이 아니거나 객체가 아니거나 대상 줄의 collected_at 이 잘못됨
    partial_last_lines: int = 0  # 줄바꿈 없이 끝난 마지막 줄(쓰는 중)
    other_target_lines: int = 0  # 다른 노선·정류장·API
    invalid_mode_lines: int = 0  # 대상 줄인데 mode 가 raw_poll CHECK 밖
    trial_lines_excluded: int = 0  # --exclude-trial 로 뺀 대상 줄
    target_lines: Counter[str] = field(default_factory=Counter)  # 대상 이름 → 줄 수
    failed_target_lines: int = 0  # 대상 줄 중 ok=false(메타데이터만 넣는다)
    position_rows: int = 0
    arrival_rows: int = 0
    arrival_empty_ranks: int = 0
    arrival_other_route_items: int = 0
    modes: Counter[str] = field(default_factory=Counter)  # 모든 줄의 mode(운영 구분 판정용)

    @property
    def target_line_total(self) -> int:
        return sum(self.target_lines.values())

    @property
    def skipped_lines(self) -> int:
        return (
            self.blank_lines
            + self.broken_lines
            + self.partial_last_lines
            + self.other_target_lines
            + self.invalid_mode_lines
            + self.trial_lines_excluded
        )


@dataclass
class DayBatch:
    day: date
    jsonl_file: str  # raw_poll.jsonl_file 값
    service_day: ServiceDayRow
    lines: list[ParsedLine]
    stats: DayStats


def iter_physical_lines(path: Path) -> Iterator[tuple[int, bytes, bool]]:
    """(줄 번호, 줄바꿈을 뗀 내용, 줄바꿈으로 끝났는지)를 한 줄씩. 파일을 통째로 읽지 않는다."""
    with path.open("rb") as fp:
        for line_no, raw in enumerate(fp, start=1):
            if raw.endswith(b"\n"):
                yield line_no, raw.rstrip(b"\r\n"), True
            else:
                yield line_no, raw, False


def read_day(data_dir: Path, day: date, *, include_trial: bool, targets: LoadTargets) -> DayBatch:
    """그날 raw_poll.jsonl 을 읽어 적재할 행을 만든다. 파일이 없으면 줄 없는 묶음(운영 none).

    파일을 읽다 OSError 가 나면 그대로 올린다(호출하는 쪽이 적재 실패로 처리).
    """
    stats = DayStats()
    lines: list[ParsedLine] = []
    path = raw_poll_path(data_dir, day)
    if not path.is_file():
        stats.file_exists = False
    else:
        for line_no, content, is_complete in iter_physical_lines(path):
            stats.lines_read += 1
            if not is_complete:
                stats.partial_last_lines += 1
                continue
            parsed = _parse_line(content, line_no, include_trial, targets, stats)
            if parsed is not None:
                lines.append(parsed)
    return DayBatch(
        day=day,
        jsonl_file=jsonl_file_name(day, RAW_POLL_FILENAME),
        service_day=build_service_day(day, stats.modes),
        lines=lines,
        stats=stats,
    )


def _parse_line(
    content: bytes, line_no: int, include_trial: bool, targets: LoadTargets, stats: DayStats
) -> ParsedLine | None:
    if not content.strip():
        stats.blank_lines += 1
        return None
    try:
        line = json.loads(content.decode("utf-8"))
    # UnicodeDecodeError·JSONDecodeError 는 ValueError. 아주 깊게 중첩된 JSON 은 RecursionError.
    except (ValueError, RecursionError):
        stats.broken_lines += 1
        return None
    if not isinstance(line, dict):
        stats.broken_lines += 1
        return None

    mode = line_mode(line)
    if mode is not None:
        stats.modes[mode] += 1
    target_name = targets.match(line)
    if target_name is None:
        stats.other_target_lines += 1
        return None
    if mode is None:
        stats.invalid_mode_lines += 1
        return None
    if mode == MODE_TRIAL and not include_trial:
        stats.trial_lines_excluded += 1
        return None
    try:
        parsed = build_line(line, line_no, mode, target_name, targets)
    except ValueError:
        stats.broken_lines += 1
        return None

    stats.target_lines[target_name] += 1
    if not parsed.raw_poll.ok:
        stats.failed_target_lines += 1
    stats.position_rows += len(parsed.positions)
    stats.arrival_rows += len(parsed.arrivals)
    stats.arrival_empty_ranks += parsed.arrival_empty_ranks
    stats.arrival_other_route_items += parsed.arrival_other_route_items
    return parsed
