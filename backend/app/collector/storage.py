"""파일 저장. 기준은 파일이다(JSONL 을 먼저 쓰고 DB 는 그다음).

- JSONL: `<데이터폴더>/<YYYY-MM-DD>/raw_poll.jsonl`, 줄마다 flush + fsync.
- 상태 파일 등: 임시 파일에 쓴 뒤 os.replace 로 교체(원자적).
- 쓰기 직전 직렬화된 문자열 전체를 Redactor 로 한 번 더 가린다.
"""

import contextlib
import json
import os
import tempfile
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.collector.gbis import CallResult
from app.collector.redact import Redactor
from app.collector.schedule import is_holiday, is_weekday, to_kst
from app.core.settings import RAW_POLL_FILENAME, REFERENCE_DIRNAME

# 윈도우에서는 다른 프로세스(status 명령 등)가 파일을 읽는 순간
# 교체가 거부될 수 있어 몇 번 다시 시도한다.
_REPLACE_RETRIES = 5
_REPLACE_RETRY_DELAY_SEC = 0.05


def raw_poll_path(data_dir: Path, day: date) -> Path:
    return data_dir / day.isoformat() / RAW_POLL_FILENAME


def reference_dir(data_dir: Path, day: date) -> Path:
    return data_dir / REFERENCE_DIRNAME / day.isoformat()


def build_record(*, collected_at: datetime, result: CallResult, mode: str) -> dict[str, Any]:
    local = to_kst(collected_at)
    day = local.date()
    return {
        "collected_at": local.isoformat(timespec="milliseconds"),
        "api": result.api,
        "params": dict(result.params),
        "http_status": result.http_status,
        "elapsed_ms": result.elapsed_ms,
        "ok": result.ok,
        "result_code": result.result_code,
        "result_message": result.result_message,
        "error": result.error,
        "is_weekday": is_weekday(day),
        "is_holiday": is_holiday(day),
        "mode": mode,
        "body": result.body,
    }


def append_jsonl(path: Path, record: dict[str, Any], redactor: Redactor) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    line = redactor.redact(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
    # 이전 프로세스가 줄 중간에 죽었으면 새 줄로 시작해 깨진 줄과 섞이지 않게 한다.
    prefix = "" if _ends_with_newline(path) else "\n"
    with path.open("a", encoding="utf-8", newline="\n") as fp:
        fp.write(prefix + line + "\n")
        fp.flush()
        os.fsync(fp.fileno())


def _ends_with_newline(path: Path) -> bool:
    """파일이 없거나 비었거나 줄바꿈으로 끝나면 True."""
    if not path.exists() or path.stat().st_size == 0:
        return True
    with path.open("rb") as fp:
        fp.seek(-1, os.SEEK_END)
        return fp.read(1) == b"\n"


def write_text_atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fp:
            fp.write(text)
            fp.flush()
            os.fsync(fp.fileno())
        _replace_with_retry(tmp_path, path)
    except BaseException:
        with contextlib.suppress(OSError):
            tmp_path.unlink()
        raise


def write_json_atomic(path: Path, obj: Any, redactor: Redactor) -> None:
    text = redactor.redact(json.dumps(obj, ensure_ascii=False, indent=2))
    write_text_atomic(path, text + "\n")


def _replace_with_retry(src: Path, dst: Path) -> None:
    for attempt in range(_REPLACE_RETRIES):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if attempt == _REPLACE_RETRIES - 1:
                raise
            time.sleep(_REPLACE_RETRY_DELAY_SEC)


def count_lines(path: Path) -> int:
    if not path.exists():
        return 0
    with path.open("rb") as fp:
        return sum(1 for _ in fp)
