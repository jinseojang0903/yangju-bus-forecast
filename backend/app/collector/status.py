"""상태 파일 `<데이터폴더>/status.json`.

하루 호출 수를 여기에 누적해 재시작해도 0 으로 돌아가지 않게 한다.
날짜(KST)가 바뀌면 새로 센다.
"""

import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.collector.redact import Redactor
from app.collector.schedule import is_holiday, is_in_window, is_weekday, to_kst
from app.collector.storage import count_lines, raw_poll_path, reference_dir, write_json_atomic
from app.core.settings import (
    CALL_LIMITS,
    COLLECT_TARGET,
    COLLECT_WINDOW,
    GBIS_ENDPOINTS,
    PLANNED_DAILY_MAX,
    REFERENCE_RECORD_SUFFIX,
    STATUS_FILENAME,
    CallLimits,
    planned_daily_calls,
    target_intervals,
)

logger = logging.getLogger(__name__)


def status_path(data_dir: Path) -> Path:
    return data_dir / STATUS_FILENAME


def _iso(moment: datetime) -> str:
    return to_kst(moment).isoformat(timespec="seconds")


def _normalize_iso(text: str | None) -> str | None:
    """JSONL 의 밀리초 시각을 상태 파일 형식(초 단위)으로 맞춘다. 읽을 수 없으면 None."""
    if not text:
        return None
    try:
        return _iso(datetime.fromisoformat(text))
    except ValueError:
        return None


def _empty_api_counter() -> dict[str, Any]:
    return {
        "calls": 0,
        "success": 0,
        "failure": 0,
        "consecutive_failures": 0,
        "capped": False,
        "capped_at": None,
    }


def read_status_file(path: Path) -> dict[str, Any] | None:
    """없으면 None. 깨져 있으면 ValueError 를 올린다."""
    if not path.exists():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("status.json 이 객체가 아니다")
    return data


_SERVICE_BY_API: dict[str, str] = {e.api: e.service for e in GBIS_ENDPOINTS}
_API_IN_BROKEN_LINE = re.compile(r'"api"\s*:\s*"([^"]+)"')


@dataclass
class RecoveredCounts:
    counters: dict[str, dict[str, int]] = field(default_factory=dict)
    last_attempt_at: str | None = None
    last_success_at: str | None = None
    unreadable_lines: int = 0


def recover_counts(jsonl: Path) -> RecoveredCounts:
    """JSONL 한 파일에서 서비스별 calls·success·failure·consecutive_failures 를 센다.

    끝까지 쓰이지 못한 줄도 호출은 일어났으므로, api 이름을 찾을 수 있으면 실패로 센다.
    """
    recovered = RecoveredCounts()

    def bump(api: str, ok: bool) -> None:
        service = _SERVICE_BY_API.get(api, api)
        counts = recovered.counters.setdefault(
            service, {"calls": 0, "success": 0, "failure": 0, "consecutive_failures": 0}
        )
        counts["calls"] += 1
        if ok:
            counts["success"] += 1
            counts["consecutive_failures"] = 0
        else:
            counts["failure"] += 1
            counts["consecutive_failures"] += 1

    with jsonl.open(encoding="utf-8", errors="replace") as fp:
        for line in fp:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                api = str(record["api"])
            except (ValueError, KeyError, TypeError):
                recovered.unreadable_lines += 1
                match = _API_IN_BROKEN_LINE.search(line)
                if match:
                    bump(match.group(1), ok=False)
                continue
            is_ok = record.get("ok") is True
            bump(api, ok=is_ok)
            collected_at = record.get("collected_at")
            if isinstance(collected_at, str):
                recovered.last_attempt_at = collected_at
                if is_ok:
                    recovered.last_success_at = collected_at
    return recovered


def recover_reference_counts(ref_dir: Path) -> dict[str, dict[str, int]]:
    """기준정보 폴더의 원본 기록(discover 호출 1건당 파일 1개)을 서비스별로 센다.

    읽을 수 없는 기록 파일도 호출은 일어났으므로 노선 API 실패로 센다.
    """
    counters: dict[str, dict[str, int]] = {}
    if not ref_dir.exists():
        return counters
    for path in sorted(ref_dir.glob(f"*{REFERENCE_RECORD_SUFFIX}")):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            service = _SERVICE_BY_API.get(str(record["api"]), str(record["api"]))
            is_ok = record.get("ok") is True
        except (OSError, ValueError, KeyError, TypeError):
            service, is_ok = CALL_LIMITS.route_service, False
        counts = counters.setdefault(
            service, {"calls": 0, "success": 0, "failure": 0, "consecutive_failures": 0}
        )
        counts["calls"] += 1
        if is_ok:
            counts["success"] += 1
            counts["consecutive_failures"] = 0
        else:
            counts["failure"] += 1
            counts["consecutive_failures"] += 1
    return counters


def build_status_report(data_dir: Path, now: datetime, *, is_running: bool) -> dict[str, Any]:
    """status 명령의 한 줄 JSON 내용.

    상태 파일 + 지금 계산한 값(창 안인지, 실행 중인지, 오늘 JSONL 줄 수).
    """
    local = to_kst(now)
    today = local.date()
    report: dict[str, Any] = {
        "now": local.isoformat(timespec="seconds"),
        "date": today.isoformat(),
        "is_weekday": is_weekday(today),
        "is_holiday": is_holiday(today),
        "in_window_now": is_in_window(local),
        "running": is_running,
        "data_dir": str(data_dir),
        "jsonl_lines_today": count_lines(raw_poll_path(data_dir, today)),
        # 지금 코드의 정식 수집 설정(배포 후 바뀌었는지 확인용).
        "config": {
            "window": f"{COLLECT_WINDOW.start:%H:%M}-{COLLECT_WINDOW.end:%H:%M}",
            "intervals": target_intervals(COLLECT_TARGET),
            "planned_daily_calls": planned_daily_calls(COLLECT_TARGET),
            "planned_daily_max": PLANNED_DAILY_MAX,
        },
    }
    try:
        saved = read_status_file(status_path(data_dir))
    except (OSError, ValueError) as exc:
        report["status_file"] = f"unreadable: {type(exc).__name__}"
        return report
    if saved is None:
        report["status_file"] = "missing"
        return report
    report["status_file"] = "today" if saved.get("date") == today.isoformat() else "stale"
    for key in (
        "date",
        "pid",
        "mode",
        "in_window",
        "interval_sec",
        "intervals",
        "planned_daily_calls",
        "updated_at",
        "last_attempt_at",
        "last_success_at",
        "apis",
        "last_error",
        "jsonl_write_failures",
        "db",
    ):
        if key in saved:
            report["status_date" if key == "date" else key] = saved[key]
    last_success = saved.get("last_success_at")
    if last_success:
        try:
            age = (local - datetime.fromisoformat(last_success)).total_seconds()
            report["last_success_age_sec"] = int(age)
        except ValueError:
            report["last_success_age_sec"] = None
    return report


class StatusStore:
    def __init__(
        self,
        path: Path,
        redactor: Redactor,
        limits: CallLimits,
        *,
        today: date,
        mode: str,
        db_enabled: bool = False,
    ) -> None:
        self._path = path
        self._redactor = redactor
        self._limits = limits
        self._mode = mode
        self._db_enabled = db_enabled
        self.data = self._load(today)

    # -- 읽기·초기화 -----------------------------------------------------------
    def _fresh(self, today: date, previous: dict[str, Any] | None = None) -> dict[str, Any]:
        return {
            "date": today.isoformat(),
            "is_weekday": is_weekday(today),
            "is_holiday": is_holiday(today),
            "in_window": False,
            "pid": os.getpid(),
            "mode": self._mode,
            "updated_at": None,
            "last_attempt_at": None,
            # 날짜가 바뀌어도 마지막 성공 시각은 남겨 살아 있는지 판단에 쓴다.
            "last_success_at": (previous or {}).get("last_success_at"),
            "daily_safe_limit_per_api": self._limits.daily_safe_limit_per_api,
            "route_daily_limit": self._limits.route_daily_limit,
            "apis": {},
            "last_error": None,
            "jsonl_write_failures": 0,
            "db": {"enabled": self._db_enabled, "failures": 0},
        }

    def _load(self, today: date) -> dict[str, Any]:
        try:
            previous = read_status_file(self._path)
        except (OSError, ValueError) as exc:
            # 0 부터 세면 한도를 넘길 수 있다. 오늘 JSONL 로 호출 수를 복구한다.
            logger.error(
                "status_unreadable path=%s error=%s action=recover_from_jsonl",
                self._path,
                type(exc).__name__,
            )
            previous = None
        if previous is None or previous.get("date") != today.isoformat():
            fresh = self._fresh(today, previous)
            self._recover_from_jsonl(fresh, today)
            return fresh
        data = previous
        data["pid"] = os.getpid()
        data["mode"] = self._mode
        data["daily_safe_limit_per_api"] = self._limits.daily_safe_limit_per_api
        data["route_daily_limit"] = self._limits.route_daily_limit
        data.setdefault("apis", {})
        data.setdefault("db", {"enabled": self._db_enabled, "failures": 0})
        data["db"]["enabled"] = self._db_enabled
        data.setdefault("jsonl_write_failures", 0)
        return data

    def _recover_from_jsonl(self, data: dict[str, Any], today: date) -> None:
        """오늘 JSONL·기준정보 원본이 있으면 API 별 호출·성공·실패 수를 다시 센다.

        파일에 남지 않은 호출(호출 중 예외)은 세지 못하므로
        실제보다 조금 적을 수 있다.
        """
        data_dir = self._path.parent
        jsonl = raw_poll_path(data_dir, today)
        recovered = recover_counts(jsonl) if jsonl.exists() else RecoveredCounts()
        # 노선 API(discover) 호출은 JSONL 이 아니라 기준정보 폴더에 남는다.
        for service, counts in recover_reference_counts(reference_dir(data_dir, today)).items():
            recovered.counters[service] = counts
        if not recovered.counters:
            return
        for service, counts in recovered.counters.items():
            data["apis"][service] = {**_empty_api_counter(), **counts}
        data["last_attempt_at"] = (
            _normalize_iso(recovered.last_attempt_at) or data["last_attempt_at"]
        )
        data["last_success_at"] = (
            _normalize_iso(recovered.last_success_at) or data["last_success_at"]
        )
        logger.warning(
            "status_recovered_from_files path=%s calls=%s unreadable_lines=%d",
            data_dir,
            {s: c["calls"] for s, c in recovered.counters.items()},
            recovered.unreadable_lines,
        )

    def roll_to(self, today: date) -> bool:
        """날짜가 바뀌었으면 카운터를 새로 시작한다. 바뀌었으면 True."""
        if self.data.get("date") == today.isoformat():
            return False
        self.data = self._fresh(today, self.data)
        return True

    # -- 카운터 ----------------------------------------------------------------
    def api(self, service: str) -> dict[str, Any]:
        counters = self.data["apis"].setdefault(service, _empty_api_counter())
        for key, value in _empty_api_counter().items():
            counters.setdefault(key, value)
        return counters

    def calls(self, service: str) -> int:
        return int(self.api(service)["calls"])

    def limit_for(self, service: str) -> int:
        return self._limits.safe_limit_for(service)

    def remaining(self, service: str) -> int:
        return max(0, self.limit_for(service) - self.calls(service))

    def can_call(self, service: str) -> bool:
        return self.calls(service) < self.limit_for(service)

    def mark_capped(self, service: str, at: datetime) -> bool:
        """처음 상한에 닿았을 때만 True(로그를 한 번만 남기려고)."""
        counters = self.api(service)
        if counters["capped"]:
            return False
        counters["capped"] = True
        counters["capped_at"] = _iso(at)
        return True

    def begin_call(self, service: str, at: datetime, *, touch_liveness: bool = True) -> None:
        """호출 수를 늘린다.

        touch_liveness=False 면 수집 생존 표시(last_attempt_at)를 건드리지 않는다(discover 용).
        """
        self.api(service)["calls"] += 1
        if touch_liveness:
            self.data["last_attempt_at"] = _iso(at)

    def end_call(
        self,
        service: str,
        *,
        ok: bool,
        error: str | None,
        at: datetime,
        touch_liveness: bool = True,
    ) -> None:
        counters = self.api(service)
        if ok:
            counters["success"] += 1
            counters["consecutive_failures"] = 0
            if touch_liveness:
                self.data["last_success_at"] = _iso(at)
        else:
            counters["failure"] += 1
            counters["consecutive_failures"] += 1
            self.record_error(service, error or "알 수 없는 오류", at)

    def record_error(self, source: str, message: str, at: datetime) -> None:
        self.data["last_error"] = {
            "at": _iso(at),
            "source": source,
            "message": self._redactor.redact(message),
        }

    def record_jsonl_failure(self, message: str, at: datetime) -> None:
        self.data["jsonl_write_failures"] = int(self.data.get("jsonl_write_failures", 0)) + 1
        self.record_error("jsonl", message, at)

    def record_db_failure(self, message: str, at: datetime) -> None:
        self.data["db"]["failures"] = int(self.data["db"].get("failures", 0)) + 1
        self.record_error("db", message, at)

    def set_in_window(self, in_window: bool) -> None:
        self.data["in_window"] = in_window

    def set_plan(
        self,
        *,
        interval_sec: int,
        intervals: dict[str, int],
        planned_daily_calls: dict[str, int] | None,
    ) -> None:
        """기본 tick(interval_sec), 대상별 주기, 오늘 예상 호출 수(정식 수집만)."""
        self.data["interval_sec"] = interval_sec
        self.data["intervals"] = intervals
        self.data["planned_daily_calls"] = planned_daily_calls

    # -- 쓰기 ------------------------------------------------------------------
    def save(self, at: datetime) -> None:
        self.data["updated_at"] = _iso(at)
        write_json_atomic(self._path, self.data, self._redactor)
