"""같은 데이터 폴더에서 수집기(run·once·save-fixture)가 둘 이상 돌지 않게 한다.

호출 수를 상태 파일에 누적하므로, 동시에 돌면 한도 계산과 상태 파일이 엉킨다.
"""

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from app.core.settings import LOCK_FILENAME


class AlreadyRunningError(RuntimeError):
    pass


def lock_path(data_dir: Path) -> Path:
    return data_dir / LOCK_FILENAME


@contextmanager
def collector_lock(data_dir: Path) -> Iterator[None]:
    """잠금을 바로 잡지 못하면 AlreadyRunningError. 기다리지 않는다."""
    data_dir.mkdir(parents=True, exist_ok=True)
    lock = FileLock(str(lock_path(data_dir)), timeout=0)
    try:
        lock.acquire()
    except Timeout as exc:
        raise AlreadyRunningError(f"이미 실행 중인 수집기가 있다: {lock_path(data_dir)}") from exc
    try:
        yield
    finally:
        lock.release()


def is_collector_running(data_dir: Path) -> bool:
    """잠금을 잠깐 잡아 보고 바로 놓는다. 잡지 못하면 다른 수집기가 돌고 있다."""
    if not data_dir.exists():
        return False
    lock = FileLock(str(lock_path(data_dir)), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        return True
    lock.release()
    return False
