"""수집기 상태 파일(<데이터폴더>/status.json) 읽기. GBIS 를 부르지 않는다.

파일 형식은 app/collector/status.py 의 StatusStore 가 정한다. 여기서는 읽기만 한다.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from app.collector.status import read_status_file, status_path

logger = logging.getLogger(__name__)

StatusFileState = Literal["missing", "unreadable", "ok"]


@dataclass(frozen=True)
class CollectorStatusRead:
    state: StatusFileState
    data: dict[str, Any] | None  # state 가 ok 일 때만


class CollectorStatusRepository:
    def __init__(self, data_dir: Path) -> None:
        self._path = status_path(data_dir)

    def read(self) -> CollectorStatusRead:
        try:
            data = read_status_file(self._path)
        except (OSError, ValueError) as exc:
            # 수집기가 쓰는 도중이거나 파일이 깨졌다. degraded 판정 근거로 넘긴다.
            logger.warning("collector_status_unreadable error=%s", type(exc).__name__)
            return CollectorStatusRead("unreadable", None)
        if data is None:
            return CollectorStatusRead("missing", None)
        return CollectorStatusRead("ok", data)
