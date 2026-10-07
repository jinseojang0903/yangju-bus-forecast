"""API 앱 로그 설정. 수집기와 같은 비밀값 가림 필터(RedactingFilter)를 단다.

'app' 로거(app.* 전체)에 표준 출력 핸들러 하나를 붙인다. 핸들러 필터는 자식 로거의 기록에도
적용되므로 app.core.errors 등 모든 API 로그가 가려진다. 기록 자체를 고치므로 위(root)로
전달되는 기록도 가려진 상태다.
"""

import logging
import sys

from app.collector.logging_setup import KstFormatter
from app.collector.redact import RedactingFilter, Redactor
from app.core.settings import Settings

_HANDLER_MARK = "_yangju_api_handler"
API_LOGGER_NAME = "app"


def setup_api_logging(settings: Settings) -> None:
    """여러 번 불러도(테스트에서 앱을 여러 번 만들어도) 핸들러가 겹치지 않는다.

    로거 레벨은 건드리지 않는다(기본은 root 의 WARNING). 같은 프로세스의 다른 로그
    설정(수집기 테스트 등)에 영향을 주지 않으려는 것이다.
    """
    logger = logging.getLogger(API_LOGGER_NAME)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(KstFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactingFilter(Redactor(settings.secret_values())))
    setattr(handler, _HANDLER_MARK, True)
    logger.addHandler(handler)

    # uvicorn 자체 오류 로그는 'app' 아래가 아니어서 위 핸들러를 거치지 않는다.
    # 그쪽에도 가림 필터를 단다.
    uvicorn_logger = logging.getLogger("uvicorn.error")
    for old_filter in list(uvicorn_logger.filters):
        if getattr(old_filter, _HANDLER_MARK, False):
            uvicorn_logger.removeFilter(old_filter)
    uvicorn_filter = RedactingFilter(Redactor(settings.secret_values()))
    setattr(uvicorn_filter, _HANDLER_MARK, True)
    uvicorn_logger.addFilter(uvicorn_filter)
