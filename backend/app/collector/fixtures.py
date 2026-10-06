"""실제 응답을 테스트 픽스처로 저장한다. 서비스 키는 어디에도 남기지 않는다."""

import json
import re
from datetime import datetime
from pathlib import Path

from app.collector.gbis import CallResult
from app.collector.redact import Redactor
from app.collector.schedule import to_kst
from app.collector.storage import write_text_atomic

_UNSAFE_NAME_CHARS = re.compile(r"[^0-9A-Za-z_-]+")


def fixture_payload(result: CallResult, redactor: Redactor) -> tuple[str, str]:
    """(본문, 확장자). JSON 은 보기 좋게, XML·기타는 원문 그대로.

    본문이 없으면 ValueError.
    """
    body = result.body
    if isinstance(body, (dict, list)):
        text, suffix = json.dumps(body, ensure_ascii=False, indent=2), ".json"
    elif isinstance(body, str):
        text, suffix = body, ".xml" if body.lstrip().startswith("<") else ".txt"
    else:
        raise ValueError("응답 본문이 없다(네트워크 오류 등)")
    return redactor.redact(text), suffix


def fixture_name(result: CallResult, at: datetime, suffix: str) -> str:
    target = "_".join(f"{k}-{v}" for k, v in result.params.items() if k != "format")
    target = _UNSAFE_NAME_CHARS.sub("_", target)
    return f"{result.api}_{target}_{to_kst(at):%Y%m%d_%H%M%S}_real{suffix}"


def save_fixture(result: CallResult, fixture_dir: Path, redactor: Redactor, at: datetime) -> Path:
    text, suffix = fixture_payload(result, redactor)
    path = fixture_dir / fixture_name(result, at, suffix)
    write_text_atomic(path, text if text.endswith("\n") else text + "\n")
    return path
