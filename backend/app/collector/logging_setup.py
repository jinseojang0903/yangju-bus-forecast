"""로그: stdout(systemd journal) + data/logs/collector.log(회전).

모든 핸들러에서 비밀값을 가린다.
"""

import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from app.collector.redact import RedactingFilter, Redactor
from app.core.settings import KST, LOG_BACKUP_COUNT, LOG_FILENAME, LOG_MAX_BYTES

_HANDLER_MARK = "_yangju_collector_handler"
# httpx 는 INFO 로 요청 URL(서비스 키 포함)을 찍는다. WARNING 이상만 남긴다.
_QUIET_LOGGERS = ("httpx", "httpcore")


class KstFormatter(logging.Formatter):
    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        return datetime.fromtimestamp(record.created, KST).isoformat(timespec="milliseconds")


def quiet_http_loggers() -> None:
    for name in _QUIET_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def setup_logging(log_dir: Path | None, redactor: Redactor, level: int = logging.INFO) -> None:
    """여러 번 불러도 핸들러가 겹치지 않는다. log_dir 이 None 이면 파일에 쓰지 않는다."""
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            root.removeHandler(handler)
            handler.close()

    formatter = KstFormatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    redacting = RedactingFilter(redactor)
    handlers: list[logging.Handler] = [logging.StreamHandler(sys.stdout)]
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(
            RotatingFileHandler(
                log_dir / LOG_FILENAME,
                maxBytes=LOG_MAX_BYTES,
                backupCount=LOG_BACKUP_COUNT,
                encoding="utf-8",
            )
        )
    for handler in handlers:
        handler.setFormatter(formatter)
        handler.addFilter(redacting)
        setattr(handler, _HANDLER_MARK, True)
        root.addHandler(handler)
    root.setLevel(level)
    quiet_http_loggers()
