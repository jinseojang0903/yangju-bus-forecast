"""백테스트 결과 출력: 마크다운 표와 운행편 × 선행시간 CSV.

분모가 0 인 비율과 건수가 0 인 Brier 는 '-' 로 쓴다(윈도우 콘솔 인코딩 cp949 가 쓸 수 있는 글자).
경로는 display_path 로만 출력한다(로컬 사용자 경로를 드러내지 않는다).

CSV 열(write_rows_csv). 값이 없으면 빈칸이다(0 과 구분된다).
- trip_key, service_date, route_id, route_name, lead_time_min: 평가 대상 운행편과 선행시간
- reference_at: 기준 시각(= 실제 도착 − L분), target_arrival_at: 목표 정류장 실제 도착
- seats, headway_min: 기준 시각 잔여석·앞차 간격(분, 소수 둘째 자리). 모르면 빈칸
- status: ok | insufficient_cases | missing_input. uncomputable_reason: missing_input 의 사유
- n, k: 사례 수와 그중 0석 사례 수. status 가 missing_input 이면 0 이며 '사례를 찾지 않았다'는
  뜻이다(사례가 0건이었다는 뜻이 아니다). 반드시 status 와 함께 본다.
- no_seat_probability: k ÷ n 을 4자리로 반올림한 값. ok 일 때만
- simple_alert(방법 A), case_alert(방법 B): 경보 1, 경보 아님 0, 판단하지 않음은 빈칸
- label: 정답(1 = 도착 상태 0석), label_grade: strict | relaxed
- case_trip_keys: 사용한 사례의 trip_key(가까운 순, ';' 구분)
- rules_version, label_rule_version: 규칙 버전
"""

import csv
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

from app.core.settings import (
    CASE_FEATURE_RULE_VERSION,
    LABEL_RULE_VERSION,
    REPO_ROOT,
    RULES_VERSION,
    ForecastRules,
    PublishCriteria,
    ServiceHours,
)
from app.evaluation.backtest import BacktestRow, Exclusion
from app.evaluation.metrics import Confusion, MethodSummary, SampleSummary, ZeroRateReference
from app.forecast.interfaces import UncomputableReason
from app.labeling.labels import Grade, UnconfirmedReason

NOT_AVAILABLE = "-"
CASE_TRIP_KEYS_SEPARATOR = ";"
CSV_COLUMNS = (
    "trip_key",
    "service_date",
    "route_id",
    "route_name",
    "lead_time_min",
    "reference_at",
    "target_arrival_at",
    "seats",
    "headway_min",
    "status",
    "uncomputable_reason",
    "n",
    "k",
    "no_seat_probability",
    "simple_alert",
    "case_alert",
    "label",
    "label_grade",
    "case_trip_keys",
    "rules_version",
    "label_rule_version",
)
# 표에 쓰는 순서. no_arrival_estimate 는 predicted_arrival 기준에서만 나오므로 넣지 않는다.
_MISSING_REASON_COLUMNS: tuple[UncomputableReason, ...] = (
    "no_target_arrival",
    "no_record_near_reference",
    "seat_unknown",
    "no_preceding_vehicle",
)
# '정답 미확인 사유' 표에 쓰는 사유. never_reached_target 은 목표 도착 기록이 없는 운행편이라
# 표본 표의 '목표 도착 기록 없음' 열에서 먼저 빠지므로 미확인으로 셀 일이 없다.
# no_record_before_target 은 도착 기록은 있으므로(첫 기록이 이미 목표 이후) 미확인에 들어간다.
_UNCONFIRMED_REASON_ROWS: tuple[UnconfirmedReason, ...] = tuple(
    reason for reason in UnconfirmedReason if reason is not UnconfirmedReason.NEVER_REACHED_TARGET
)


def display_path(path: Path) -> str:
    """출력용 경로: 저장소 안이면 저장소 기준 상대 경로(/ 구분), 밖이면 마지막 이름만."""
    resolved = path.resolve()
    if resolved.is_relative_to(REPO_ROOT):
        return resolved.relative_to(REPO_ROOT).as_posix() or "."
    return resolved.name or "."


@dataclass(frozen=True)
class RunInfo:
    """실행 조건과 입력 읽기 결과(리포트 머리말)."""

    date_from: date
    date_to: date
    data_dir: Path
    grades: Sequence[Grade]
    read_days: Sequence[date]  # 파일을 읽은 날(날짜순)
    missing_days: Sequence[date]  # 평일인데 파일이 없어 건너뛴 날
    weekend_days: Sequence[date]  # 수집 요일이 아니라 읽지 않은 날
    weekend_days_with_file: Sequence[date]  # 그중 파일이 있었던 날(읽지 않았다)
    non_service_days: Sequence[date]  # 읽은 날 중 공휴일(운행편은 사례·평가에서 뺀다)
    seq_mismatch_days: Sequence[date]  # 목표 순번이 위치 기록의 stationId 와 맞지 않은 날
    lines: int = 0
    location_lines: int = 0
    failed_location_lines: int = 0
    broken_lines: int = 0
    trial_records: int = 0  # 목표 노선 위치 기록 중 시운전(mode=trial) 기록 수
    items: int = 0
    skipped_items: int = 0


def _dates(days: Sequence[date]) -> str:
    return ", ".join(day.isoformat() for day in days) if days else "없음"


def _percent(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value * 100:.1f}%"


def _score(value: float | None) -> str:
    return NOT_AVAILABLE if value is None else f"{value:.4f}"


def _table(header: Sequence[object], rows: Iterable[Sequence[object]]) -> list[str]:
    def line(cells: Sequence[object]) -> str:
        return "| " + " | ".join(str(cell) for cell in cells) + " |"

    return [line(header), line(["---"] * len(header)), *(line(row) for row in rows)]


def _confusion_cells(confusion: Confusion) -> list[object]:
    return [
        confusion.tp,
        confusion.fn,
        confusion.fp,
        confusion.tn,
        _percent(confusion.precision),
        _percent(confusion.recall),
        _score(confusion.brier),
    ]


_CONFUSION_HEADER = (
    "맞힌 만차(TP)",
    "놓친 만차(FN)",
    "잘못 알린 만차(FP)",
    "맞힌 비만차(TN)",
    "정밀도",
    "재현율",
    "Brier",
)


def _reference_cells(reference: ZeroRateReference) -> list[object]:
    return [
        reference.count,
        reference.zero_count,
        _percent(reference.zero_rate),
        _score(reference.brier),
    ]


def _format_header(info: RunInfo, rules: ForecastRules, criteria: PublishCriteria) -> list[str]:
    observed = (
        f"{info.read_days[0].isoformat()} ~ {info.read_days[-1].isoformat()}"
        if info.read_days
        else "없음"
    )
    lines = [
        "# 백테스트: 단순 기준(방법 A)과 사례 방식(방법 B) 비교",
        "",
        f"- 요청 기간: {info.date_from.isoformat()} ~ {info.date_to.isoformat()}",
        f"- 관측 기간(실제 읽은 첫날 ~ 마지막 날): {observed}",
        f"- 읽은 평일 수: {len(info.read_days)}일"
        f" (그중 공휴일 {len(info.non_service_days)}일: {_dates(info.non_service_days)})",
        f"- 건너뛴 날짜(평일인데 수집 파일 없음) {len(info.missing_days)}일:"
        f" {_dates(info.missing_days)}",
        f"- 주말 {len(info.weekend_days)}일은 수집 요일이 아니라 읽지 않음"
        f" (파일이 있었던 날: {_dates(info.weekend_days_with_file)})",
        f"- 데이터 폴더: {display_path(info.data_dir)}",
        f"- 입력: 줄 {info.lines} (위치 {info.location_lines}, 위치 실패"
        f" {info.failed_location_lines}, 깨진 줄 {info.broken_lines})"
        f" / 차량 항목 {info.items} (건너뜀 {info.skipped_items})"
        f" / 목표 노선 기록 중 시운전 {info.trial_records}건",
        f"- 정답 등급: {'+'.join(grade.value for grade in info.grades)}",
        f"- 규칙 버전: rules={RULES_VERSION}, case_feature={CASE_FEATURE_RULE_VERSION},"
        f" label={LABEL_RULE_VERSION}",
        f"- 사례 검색: 허용폭 잔여석 ±{rules.seat_tolerance}석"
        f"·간격 ±{rules.headway_tolerance_min}분,"
        f" 최대 {rules.max_cases}건, {rules.min_cases_to_show}건 미만이면 확률을 내지 않음",
        f"- 방법 A: 도착 L분 전 잔여석이 0 이면 경보 / 방법 B: 사례 비율 k/n 이"
        f" {criteria.alert_probability_min} 이상이면 경보",
        "- 이 표는 숫자만 낸다. 공개 판정(pass/fail)은 하지 않는다.",
        "- 한계: 기준 시각은 실제 도착 − L분이다. 실서비스는 도착 예상으로 정하므로"
        " 이 수치는 실서비스 성능보다 유리할 수 있다.",
    ]
    if info.seq_mismatch_days:
        lines.append(
            "- 경고: 목표 순번이 위치 기록의 stationId 와 맞지 않은 날"
            f" {_dates(info.seq_mismatch_days)}."
            " 라벨과 특징을 믿기 전에 순번을 확인한다(python -m app.labeling report)."
        )
    return lines


def _format_sample(samples: Sequence[SampleSummary], hours: ServiceHours) -> list[str]:
    hours_text = (
        f"{hours.forecast_arrival_start:%H:%M} 이상 {hours.forecast_arrival_end:%H:%M} 미만"
    )
    lines = [
        "",
        "## 1. 표본",
        "",
        f"평가 대상 = 시운전·공휴일이 아니고, 목표 정류장 도착이 {hours_text}이며,"
        " 정답이 확정된 운행편.",
        "앞 열에서 빠진 운행편은 뒤 열에서 다시 세지 않는다.",
        "",
        *_table(
            (
                "노선",
                "운행편",
                "시운전",
                "공휴일·주말",
                "목표 도착 기록 없음",
                "예보 시간대 밖 도착",
                "정답 미확인",
                "쓰지 않는 등급",
                "평가 대상",
                "실제 0석 도착",
                "strict",
                "relaxed",
            ),
            (
                (
                    s.route_name,
                    s.trips,
                    s.by_exclusion[Exclusion.TRIAL],
                    s.by_exclusion[Exclusion.NON_SERVICE_DAY],
                    s.by_exclusion[Exclusion.NO_TARGET_ARRIVAL],
                    s.by_exclusion[Exclusion.OUTSIDE_FORECAST_HOURS],
                    s.by_exclusion[Exclusion.UNCONFIRMED],
                    s.by_exclusion[Exclusion.GRADE_NOT_USED],
                    s.targets,
                    s.zero_arrivals,
                    s.by_grade[Grade.STRICT],
                    s.by_grade[Grade.RELAXED],
                )
                for s in samples
            ),
        ),
        "",
        "정답 미확인으로 뺀 운행편의 사유(비율 = 건수 ÷ 예보 시간대에 도착 기록이 있는 운행편)",
        "",
        *_table(
            ("노선", "사유", "건수", "비율"),
            (
                (
                    s.route_name,
                    reason.value,
                    s.unconfirmed_reasons[reason],
                    _percent(
                        s.unconfirmed_reasons[reason] / s.in_hours_arrivals
                        if s.in_hours_arrivals
                        else None
                    ),
                )
                for s in samples
                for reason in _UNCONFIRMED_REASON_ROWS
            ),
        ),
    ]
    return lines


def _format_methods(methods: Sequence[MethodSummary]) -> list[str]:
    return [
        "",
        "## 2. 방법 B (사례 방식)",
        "",
        "제공 = 확률을 낸 운행편. 보류 = 확률을 내지 않은 운행편."
        " TP~Brier 는 제공한 것만으로 계산.",
        "",
        *_table(
            ("선행시간", "노선", "평가 대상", "제공", "보류(사례 부족)", "보류(입력 누락)")
            + _CONFUSION_HEADER,
            (
                [
                    f"{m.lead_time_min}분",
                    m.route_name,
                    m.targets,
                    m.case_provided.total,
                    m.case_insufficient,
                    m.case_missing_input,
                    *_confusion_cells(m.case_provided),
                ]
                for m in methods
            ),
        ),
        "",
        "입력 누락 사유(방법 B 가 특징을 계산하지 못한 운행편)",
        "",
        *_table(
            ("선행시간", "노선", *_MISSING_REASON_COLUMNS),
            (
                [
                    f"{m.lead_time_min}분",
                    m.route_name,
                    *(m.missing_reasons.get(reason, 0) for reason in _MISSING_REASON_COLUMNS),
                ]
                for m in methods
            ),
        ),
        "",
        "## 3. 방법 A (단순 기준: 도착 L분 전 잔여석 0 이면 경보)",
        "",
        "Brier 는 0/1 판단을 그대로 확률로 본 값이다.",
        "",
        "### (a) 기준 시각 잔여석을 아는 전체 운행편",
        "",
        *_table(
            ("선행시간", "노선", "평가 대상", "판단", "판단 불가(잔여석 모름)") + _CONFUSION_HEADER,
            (
                [
                    f"{m.lead_time_min}분",
                    m.route_name,
                    m.targets,
                    m.simple_decided.total,
                    m.simple_undecided,
                    *_confusion_cells(m.simple_decided),
                ]
                for m in methods
            ),
        ),
        "",
        "### (b) 방법 B 가 확률을 낸 같은 운행편만",
        "",
        *_table(
            ("선행시간", "노선", "운행편") + _CONFUSION_HEADER,
            (
                [
                    f"{m.lead_time_min}분",
                    m.route_name,
                    m.simple_on_case_provided.total,
                    *_confusion_cells(m.simple_on_case_provided),
                ]
                for m in methods
            ),
        ),
        "",
        "## 4. 참고: 평균 비율 예측의 Brier",
        "",
        "같은 대상의 0석 도착 비율을 모든 운행편의 예측으로 그대로 썼을 때의 Brier 다.",
        "비율을 평가 대상 자신의 정답으로 계산한 값이라 예보 시점에는 알 수 없다.",
        "기획안의 기준선과 같은 정의인지 정해지지 않았다. 기획안 정의 확인 필요.",
        "",
        *_table(
            (
                "선행시간",
                "노선",
                "전체: 운행편",
                "전체: 0석 도착",
                "전체: 0석 비율",
                "전체: Brier",
                "B 제공: 운행편",
                "B 제공: 0석 도착",
                "B 제공: 0석 비율",
                "B 제공: Brier",
            ),
            (
                [
                    f"{m.lead_time_min}분",
                    m.route_name,
                    *_reference_cells(m.reference_all),
                    *_reference_cells(m.reference_case_provided),
                ]
                for m in methods
            ),
        ),
    ]


def format_report(
    *,
    info: RunInfo,
    samples: Sequence[SampleSummary],
    methods: Sequence[MethodSummary],
    rules: ForecastRules,
    criteria: PublishCriteria,
    hours: ServiceHours,
) -> list[str]:
    """백테스트 리포트(마크다운) 줄 목록. 표본이 0건인 칸은 0 또는 '-' 로 둔다."""
    return [
        *_format_header(info, rules, criteria),
        *_format_sample(samples, hours),
        *_format_methods(methods),
    ]


def _iso(moment: datetime | None) -> str:
    return moment.isoformat(timespec="seconds") if moment is not None else ""


def _blank_if_none(value: object | None) -> object:
    return "" if value is None else value


def _alert_text(is_alert: bool | None) -> str:
    """경보 1, 경보 아님 0, 판단하지 않음(판단 불가·보류)은 빈칸."""
    return "" if is_alert is None else str(int(is_alert))


def write_rows_csv(path: Path, rows: Iterable[BacktestRow], route_names: Mapping[str, str]) -> int:
    """운행편 × 선행시간 행을 CSV(UTF-8 BOM, 엑셀용)로 쓴다. 돌려주는 값: 쓴 행 수.

    실패 사례를 사람이 찾아볼 수 있게 입력값·판단·정답·사용한 사례를 한 줄에 둔다.
    값이 없는 칸은 빈칸이다(0 과 구분된다). 열의 뜻은 모듈 설명에 있다. 특히 status 가
    missing_input 이면 n·k 는 0 이며 계산하지 않았다는 뜻이므로 status 와 함께 본다.
    파일이 있으면 덮어쓴다(덮어쓸지는 호출하는 쪽이 정한다). 쓰지 못하면 OSError 를 그대로 올린다.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8-sig", newline="") as fp:
        writer = csv.writer(fp)
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            forecast = row.forecast
            writer.writerow(
                (
                    forecast.trip_key,
                    forecast.service_date.isoformat(),
                    forecast.route_id,
                    route_names.get(forecast.route_id, forecast.route_id),
                    forecast.lead_time_min,
                    _iso(forecast.issued_at),
                    _iso(row.target_arrival_at),
                    _blank_if_none(row.reference_seats),
                    "" if row.headway_min is None else f"{row.headway_min:.2f}",
                    forecast.status,
                    row.uncomputable_reason or "",
                    _blank_if_none(forecast.n),
                    _blank_if_none(forecast.k),
                    _blank_if_none(forecast.no_seat_probability),
                    _alert_text(row.simple_alert),
                    _alert_text(row.case_alert),
                    _blank_if_none(forecast.label),
                    forecast.label_grade or "",
                    CASE_TRIP_KEYS_SEPARATOR.join(forecast.case_trip_keys),
                    forecast.rules_version,
                    forecast.label_rule_version,
                )
            )
            count += 1
    return count
