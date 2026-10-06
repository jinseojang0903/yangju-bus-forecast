"""운행편 라벨 집계·표 출력·CSV 저장.

표의 열 이름은 터미널에서 줄이 맞도록 영문 코드로 쓴다(등급·사유 코드와 같은 말).
"""

import csv
from collections import Counter
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

from app.labeling.labels import NOT_REACHED_REASONS, Grade, TripLabel, UnconfirmedReason
from app.labeling.records import ReadStats
from app.labeling.targets import TargetSeqCheck

TOTAL_ROUTE_NAME = "ALL"
CSV_COLUMNS = (
    "service_date",
    "route_id",
    "route_name",
    "veh_id",
    "plate_no",
    "trip_index",
    "trip_start_at",
    "target_seq",
    "target_arrival_at",
    "grade",
    "label",
    "last_seat",
    "last_seat_at",
    "reason",
    "is_trial",
    "is_holiday",
    "interval_sec",
    "record_count",
)


def select_labels(
    labels: Iterable[TripLabel],
    *,
    grades: Collection[Grade],
    include_trial: bool,
    include_holiday: bool,
) -> list[TripLabel]:
    """사례·평가에 쓸 라벨만 고른다. 미확인은 grades 에 넣어도 항상 뺀다."""
    return [
        lb
        for lb in labels
        if lb.is_confirmed
        and lb.grade in grades
        and (include_trial or not lb.is_trial)
        and (include_holiday or not lb.is_holiday)
    ]


@dataclass
class RouteSummary:
    route_id: str
    route_name: str
    trips: int = 0
    by_grade: Counter[Grade] = field(default_factory=Counter)
    zero_by_grade: Counter[Grade] = field(default_factory=Counter)  # label == 1
    by_reason: Counter[UnconfirmedReason] = field(default_factory=Counter)
    trial_trips: int = 0
    holiday_trips: int = 0

    @property
    def reached_trips(self) -> int:
        """목표 정류장에 도착한 운행편 수(제외 비율의 분모)."""
        return self.trips - sum(self.by_reason[r] for r in NOT_REACHED_REASONS)

    def add(self, label: TripLabel) -> None:
        self.trips += 1
        self.by_grade[label.grade] += 1
        if label.label == 1:
            self.zero_by_grade[label.grade] += 1
        if label.reason is not None:
            self.by_reason[label.reason] += 1
        self.trial_trips += int(label.is_trial)
        self.holiday_trips += int(label.is_holiday)


def summarize(labels: Iterable[TripLabel], route_names: Mapping[str, str]) -> list[RouteSummary]:
    """route_names 순서대로 노선별 집계, 마지막에 합계(ALL). 라벨이 없는 노선도 0 으로 넣는다."""
    by_route = {rid: RouteSummary(rid, name) for rid, name in route_names.items()}
    total = RouteSummary("", TOTAL_ROUTE_NAME)
    for label in labels:
        summary = by_route.get(label.route_id)
        if summary is None:
            summary = by_route[label.route_id] = RouteSummary(label.route_id, label.route_id)
        summary.add(label)
        total.add(label)
    return [*by_route.values(), total]


def _rate(count: int, denominator: int) -> str:
    return f"{count / denominator * 100:.1f}%" if denominator > 0 else "-"


def _row(cells: Sequence[object], widths: Sequence[int]) -> str:
    first, *rest = (str(c) for c in cells)
    parts = [first.ljust(widths[0])] + [c.rjust(w) for c, w in zip(rest, widths[1:], strict=True)]
    return "  ".join(parts).rstrip()


def _format_intervals(values: Collection[int | None]) -> str:
    known = sorted(v for v in values if v is not None)
    text = ",".join(str(v) for v in known)
    if None in values:
        text = f"{text},-" if text else "-"
    return text or "-"


def _format_target_check(check: TargetSeqCheck) -> str:
    stop = check.stop
    if not check.observed_seqs:
        observed = "관측 없음"
    else:
        seqs = ", ".join(f"{seq}({n}건)" for seq, n in check.observed_seqs.items())
        observed = ("불일치 " if check.is_observed_mismatch else "일치 ") + seqs
    if check.reference_seq is None:
        reference = "항목 없음"
    elif check.is_reference_mismatch:
        reference = f"불일치 {check.reference_seq}"
    else:
        reference = "일치"
    return (
        f"  {stop.route_name} routeId={stop.route_id} 순번 {stop.station_seq}"
        f" | 위치 기록 stationId 확인: {observed} | targets.json: {reference}"
    )


def format_report(
    *,
    day: date,
    source: Path,
    include_trial: bool,
    stats: ReadStats,
    target_station_id: str,
    checks: Sequence[TargetSeqCheck],
    targets_file: Path | None,
    targets_file_error: str | None,
    summaries: Sequence[RouteSummary],
    unlabeled_trips: int,
    gap_sec: float,
    seq_drop: int,
) -> list[str]:
    lines = [
        f"[운행편 라벨 리포트] date={day.isoformat()} include_trial={str(include_trial).lower()}",
        f"입력 파일: {source}",
        (
            f"입력: 줄 {stats.lines} (위치 {stats.location_lines}, 위치 실패 "
            f"{stats.failed_location_lines}, trial 제외 {stats.trial_lines_skipped}, 깨진 줄 "
            f"{stats.broken_lines}) / 차량 항목 {stats.items} (건너뜀 {stats.skipped_items})"
            f" / interval_sec {_format_intervals(stats.interval_secs)}"
        ),
        f"운행편 분리 기준: 공백 > {int(gap_sec)}초 또는 순번 감소 >= {seq_drop}",
        f"목표 정류장 stationId={target_station_id}"
        f" (targets.json: {targets_file if targets_file else '없음'})",
    ]
    if targets_file_error:
        lines.append(f"  targets.json 읽기 실패: {targets_file_error}")
    lines.extend(_format_target_check(c) for c in checks)
    if any(c.is_mismatch for c in checks):
        lines.append("  경고: 목표 순번이 근거와 다르다. 라벨을 쓰기 전에 순번을 확인한다.")
    if unlabeled_trips:
        lines.append(f"  목표 정류장이 정해지지 않은 노선의 운행편 {unlabeled_trips}건은 뺐다.")

    grade_widths = (6, 6, 7, 8, 12, 12, 13)
    lines += [
        "",
        "[노선별 등급] zero_* = 도착 상태 0석(label=1) 건수",
        _row(
            (
                "route",
                "trips",
                "strict",
                "relaxed",
                "unconfirmed",
                "zero_strict",
                "zero_relaxed",
            ),
            grade_widths,
        ),
    ]
    for s in summaries:
        lines.append(
            _row(
                (
                    s.route_name,
                    s.trips,
                    s.by_grade[Grade.STRICT],
                    s.by_grade[Grade.RELAXED],
                    s.by_grade[Grade.UNCONFIRMED],
                    s.zero_by_grade[Grade.STRICT],
                    s.zero_by_grade[Grade.RELAXED],
                ),
                grade_widths,
            )
        )

    reason_widths = (6, 30, 6, 9, 13)
    lines += [
        "",
        "[미확인 사유] rate_all = 건수/전체 운행편, rate_reached = 건수/목표 도착 운행편",
        _row(("route", "reason", "count", "rate_all", "rate_reached"), reason_widths),
    ]
    for s in summaries:
        for reason in UnconfirmedReason:
            count = s.by_reason[reason]
            reached = "-" if reason in NOT_REACHED_REASONS else _rate(count, s.reached_trips)
            lines.append(
                _row(
                    (s.route_name, reason.value, count, _rate(count, s.trips), reached),
                    reason_widths,
                )
            )

    flag_widths = (6, 6, 6, 8)
    lines += [
        "",
        "[플래그] 등급은 그대로 계산하고 따로 센다",
        _row(("route", "trips", "trial", "holiday"), flag_widths),
    ]
    for s in summaries:
        lines.append(_row((s.route_name, s.trips, s.trial_trips, s.holiday_trips), flag_widths))
    return lines


def _iso(moment: datetime | None) -> str:
    return moment.isoformat(timespec="seconds") if moment is not None else ""


def write_labels_csv(
    path: Path, labels: Iterable[TripLabel], route_names: Mapping[str, str]
) -> int:
    """운행편별 라벨을 CSV(UTF-8 BOM, 엑셀용)로 쓴다. 돌려주는 값: 쓴 행 수."""
    path.parent.mkdir(parents=True, exist_ok=True)
    rows = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(CSV_COLUMNS)
        for lb in labels:
            writer.writerow(
                (
                    lb.service_date.isoformat(),
                    lb.route_id,
                    route_names.get(lb.route_id, lb.route_id),
                    lb.veh_id,
                    lb.plate_no or "",
                    lb.trip_index,
                    _iso(lb.trip_start_at),
                    lb.target_seq,
                    _iso(lb.target_arrival_at),
                    lb.grade.value,
                    "" if lb.label is None else lb.label,
                    "" if lb.last_seat is None else lb.last_seat,
                    _iso(lb.last_seat_at),
                    lb.reason.value if lb.reason else "",
                    str(lb.is_trial).lower(),
                    str(lb.is_holiday).lower(),
                    "/".join(str(v) for v in lb.interval_secs),
                    lb.record_count,
                )
            )
            rows += 1
    return rows
