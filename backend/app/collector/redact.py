"""비밀값 가리기.

서비스 키는 URL 에 쿼리로 실리므로, 예외 메시지·로그에
원문 또는 퍼센트 인코딩 형태로 섞일 수 있다.
파일(JSONL·상태·픽스처)과 로그로 나가는 문자열은 모두 여기를 거친다.
"""

import logging
import re
from collections.abc import Iterable
from urllib.parse import quote, quote_plus, unquote

MASK = "***"
# 너무 짧은 값은 일반 문자열을 망가뜨릴 수 있어 가리지 않는다(실제 키는 수십 자).
_MIN_SECRET_LENGTH = 6
_SERVICE_KEY_PARAM = re.compile(r"(?i)(servicekey=)[^&\s\"'<>]+")


def _variants(secret: str) -> set[str]:
    forms = {
        secret,
        quote(secret, safe=""),
        quote(secret),
        quote_plus(secret, safe=""),
        quote(quote(secret, safe=""), safe=""),  # 두 번 인코딩된 형태
    }
    if "%" in secret:
        forms.add(unquote(secret))
    return {f for f in forms if len(f) >= _MIN_SECRET_LENGTH}


class Redactor:
    def __init__(self, secrets: Iterable[str | None] = ()) -> None:
        forms: set[str] = set()
        for secret in secrets:
            if secret and len(secret) >= _MIN_SECRET_LENGTH:
                forms |= _variants(secret)
        # 긴 형태부터 바꿔야 인코딩형 안의 일부만 바뀌는 일이 없다.
        ordered = sorted(forms, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(re.escape(f) for f in ordered), re.IGNORECASE) if ordered else None
        )

    def redact(self, text: str) -> str:
        if not text:
            return text
        if self._pattern is not None:
            text = self._pattern.sub(MASK, text)
        return _SERVICE_KEY_PARAM.sub(lambda m: m.group(1) + MASK, text)

    def redact_optional(self, text: str | None) -> str | None:
        return None if text is None else self.redact(text)


def describe_exception(exc: BaseException, redactor: Redactor) -> str:
    """예외를 '형식: 메시지' 한 줄로 바꾸고 비밀값을 가린다. 스택은 넣지 않는다."""
    message = str(exc).replace("\r", " ").replace("\n", " ")
    return redactor.redact(f"{type(exc).__name__}: {message}" if message else type(exc).__name__)


class RedactingFilter(logging.Filter):
    """핸들러에 붙여 모든 로그(외부 라이브러리 포함)의 메시지와 예외 문자열을 가린다."""

    def __init__(self, redactor: Redactor) -> None:
        super().__init__()
        self._redactor = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            # 형식 문자열과 인자가 맞지 않아도 로그 호출이 수집 루프를 깨뜨리지 않게 한다.
            message = f"{record.msg} args={record.args!r}"
        record.msg = self._redactor.redact(message)
        record.args = None
        if record.exc_info and not record.exc_text:
            record.exc_text = logging.Formatter().formatException(record.exc_info)
        if record.exc_text:
            record.exc_text = self._redactor.redact(record.exc_text)
        return True
