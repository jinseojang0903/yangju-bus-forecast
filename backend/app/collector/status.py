"""상태 파일 `<데이터폴더>/status.json`.

하루 호출 수를 여기에 누적해 재시작해도 0 으로 돌아가지 않게 한다.
날짜(KST)가 바뀌면 새로 센다.
StatusStore 자체는 잠그지 않는다. 여러 작업자가 쓰면 호출하는 쪽(Collector)이 잠근다.
"""

import copy
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
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
        "quota_exceeded": 0,  # 포털 하루 호출량 초과 응답 수
        "quota_exceeded_at": None,  # 오늘 처음 받은 시각
        "quota_exceeded_consecutive": 0,  # 정상 응답 없이 이어진 호출량 초과 응답 수
        "throttled": False,  # True 면 이 API 는 시험 호출 간격으로만 부른다
        "throttled_at": None,
        "next_probe_at": None,
    }


def _empty_target_counter(interval_sec: int) -> dict[str, Any]:
    return {
        "interval_sec": interval_sec,
        "calls": 0,
        "success": 0,
        "failure": 0,
        "consecutive_failures": 0,
        "empty": 0,  # 빈 응답 수(성공에 포함된다)
        "skipped_cycles": 0,  # 늦어서 건너뛴 주기 수
        "cap_skips": 0,  # 안전 상한 때문에 부르지 않은 수
        "throttle_skips": 0,  # 호출량 초과 감속 중이라 부르지 않은 수
        "last_attempt_at": None,
        "last_success_at": None,
    }


def config_report() -> dict[str, Any]:
    """지금 코드의 정식 수집 설정(배포 후 바뀌었는지 확인용)."""
    planned = planned_daily_calls(COLLECT_TARGET)
    limits = CALL_LIMITS.as_dict()
    return {
        "window": f"{COLLECT_WINDOW.start:%H:%M}-{COLLECT_WINDOW.end:%H:%M}",
        "intervals": target_intervals(COLLECT_TARGET),
        "apis": {
            service: {"planned": planned.get(service, 0), **limits[service]}
            for service in limits
            if service in planned
        },
        "not_collected": [r.route_name for r in COLLECT_TARGET.route_catalog if not r.collect],
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
        # 지금 코드의 정식 수집 설정: 창, 대상별 주기, API 별 예상 호출·상한.
        "config": config_report(),
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
        "limits",
        "updated_at",
        "last_attempt_at",
        "last_success_at",
        "apis",
        "targets",
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
            "limits": self._limits.as_dict(),
            "route_daily_limit": self._limits.route_daily_limit,
            "apis": {},
            # 대상(위치 노선·도착 정류장)별 오늘 호출·성공·실패·건너뜀. 키는 'location:G1300' 등.
            "targets": {},
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
        data.pop("daily_safe_limit_per_api", None)  # 이전 형식(모든 API 980)
        data["limits"] = self._limits.as_dict()
        data["route_daily_limit"] = self._limits.route_daily_limit
        data.setdefault("apis", {})
        if not isinstance(data.get("targets"), dict):
            data["targets"] = {}
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

    def record_quota_exceeded(self, service: str, at: datetime) -> bool:
        """포털 하루 호출량 초과 응답을 센다. 오늘 처음이면 True(크게 기록하려고)."""
        counters = self.api(service)
        counters["quota_exceeded"] = int(counters["quota_exceeded"]) + 1
        if counters["quota_exceeded_at"] is None:
            counters["quota_exceeded_at"] = _iso(at)
            return True
        return False

    # -- 대상별 카운터 ------------------------------------------------------------
    def target(self, key: str, interval_sec: int) -> dict[str, Any]:
        targets = self.data.setdefault("targets", {})
        counters = targets.setdefault(key, _empty_target_counter(interval_sec))
        for name, value in _empty_target_counter(interval_sec).items():
            counters.setdefault(name, value)
        counters["interval_sec"] = interval_sec
        return counters

    def begin_target_call(self, key: str, interval_sec: int, at: datetime) -> None:
        counters = self.target(key, interval_sec)
        counters["calls"] += 1
        counters["last_attempt_at"] = _iso(at)

    def end_target_call(
        self, key: str, interval_sec: int, *, ok: bool, at: datetime, is_empty: bool = False
    ) -> None:
        counters = self.target(key, interval_sec)
        if ok:
            counters["success"] += 1
            counters["consecutive_failures"] = 0
            counters["last_success_at"] = _iso(at)
            if is_empty:
                counters["empty"] += 1
        else:
            counters["failure"] += 1
            counters["consecutive_failures"] += 1

    def add_target_skipped(self, key: str, interval_sec: int, count: int) -> None:
        self.target(key, interval_sec)["skipped_cycles"] += count

    def add_target_cap_skip(self, key: str, interval_sec: int) -> None:
        self.target(key, interval_sec)["cap_skips"] += 1

    def add_target_throttle_skip(self, key: str, interval_sec: int) -> None:
        self.target(key, interval_sec)["throttle_skips"] += 1

    # -- 호출량 초과 감속 ----------------------------------------------------------
    def take_probe(self, service: str, now: datetime, probe_interval_sec: int) -> bool:
        """감속 중이 아니면 True.

        감속 중이면 시험 호출 시각이 됐을 때만 True 이고, 그때 다음 시험 호출 시각을 잡는다.
        """
        counters = self.api(service)
        if not counters["throttled"]:
            return True
        next_probe = counters["next_probe_at"]
        if next_probe is not None and to_kst(now) < datetime.fromisoformat(next_probe):
            return False
        counters["next_probe_at"] = _iso(to_kst(now) + timedelta(seconds=probe_interval_sec))
        return True

    def record_quota_outcome(
        self,
        service: str,
        *,
        is_quota_exceeded: bool,
        ok: bool,
        at: datetime,
        throttle_after: int,
        probe_interval_sec: int,
    ) -> str | None:
        """호출량 초과 연속 수를 갱신한다. 감속을 시작하면 'started', 풀면 'ended'.

        초과 응답은 연속 수를 올리고, 정상 응답(ok)은 0 으로 되돌리며 감속을 푼다.
        그 밖의 실패(네트워크 오류 등)는 연속 수를 바꾸지 않는다.
        """
        counters = self.api(service)
        if is_quota_exceeded:
            counters["quota_exceeded_consecutive"] = int(counters["quota_exceeded_consecutive"]) + 1
            if (
                not counters["throttled"]
                and counters["quota_exceeded_consecutive"] >= throttle_after
            ):
                counters["throttled"] = True
                counters["throttled_at"] = _iso(at)
                counters["next_probe_at"] = _iso(to_kst(at) + timedelta(seconds=probe_interval_sec))
                return "started"
            return None
        if not ok:
            return None
        counters["quota_exceeded_consecutive"] = 0
        if counters["throttled"]:
            counters["throttled"] = False
            counters["next_probe_at"] = None
            return "ended"
        return None

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
        self.write(self.snapshot(at))

    def snapshot(self, at: datetime) -> dict[str, Any]:
        """updated_at 을 at 으로 하고 지금 내용을 복사한다.

        여러 작업자가 쓸 때는 잠금 안에서 부르고, 파일 쓰기(write)는 잠금 밖에서 한다.
        """
        self.data["updated_at"] = _iso(at)
        return copy.deepcopy(self.data)

    def write(self, snapshot: dict[str, Any]) -> None:
        write_json_atomic(self._path, snapshot, self._redactor)
