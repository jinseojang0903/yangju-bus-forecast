"""공통 에러 형식과 예외 처리기(계약 1.4절).

메시지는 고정 문자열이다. 입력값·내부 예외 내용·경로를 응답에 담지 않는다.
FastAPI 기본 422(요청 검증 실패)는 400 VALIDATION_FAILED 로 바꾼다.
"""

import logging
from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.schemas.common import ErrorCode

logger = logging.getLogger(__name__)

ERROR_MESSAGES: dict[ErrorCode, str] = {
    "VALIDATION_FAILED": "요청 형식이 올바르지 않습니다",
    "NOT_FOUND": "요청한 대상을 찾을 수 없습니다",
    "METHOD_NOT_ALLOWED": "허용되지 않은 요청 방식입니다",
    "RATE_LIMITED": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요",
    "INTERNAL_ERROR": "서버 내부 오류가 발생했습니다",
}

# 검증 실패 사유(고정 문구). 필드별 형식 안내가 있으면 그것을 쓴다.
_FORMAT_REASON_BY_FIELD: dict[str, str] = {
    "station": "숫자 1–20자리여야 합니다",
    "stationId": "숫자 1–20자리여야 합니다",
    "destination": "영문 소문자와 _ 1–32자여야 합니다",
    "deadline": "HH:MM 형식(00:00–23:59)이어야 합니다",
    "snapshotId": "uuid 형식이어야 합니다",
    "text": "1–200자여야 합니다",
}
_REASON_BY_ERROR_TYPE: dict[str, str] = {
    "missing": "필수 값입니다",
    "json_invalid": "JSON 형식이 올바르지 않습니다",
    "model_attributes_type": "JSON 객체여야 합니다",
    "dict_type": "JSON 객체여야 합니다",
    "string_type": "문자열이어야 합니다",
}
_FORMAT_ERROR_TYPES = frozenset(
    {"string_pattern_mismatch", "string_too_short", "string_too_long", "uuid_parsing", "uuid_type"}
)
_DEFAULT_REASON = "값이 올바르지 않습니다"


class AppError(Exception):
    """경계에서 공통 에러 형식으로 바뀌는 예외."""

    status_code = 500
    code: ErrorCode = "INTERNAL_ERROR"

    def __init__(self, details: Sequence[dict[str, str]] = ()) -> None:
        super().__init__(self.code)
        self.details = list(details)


class ValidationFailedError(AppError):
    status_code = 400
    code: ErrorCode = "VALIDATION_FAILED"


class NotFoundError(AppError):
    status_code = 404
    code: ErrorCode = "NOT_FOUND"


class RateLimitedError(AppError):
    status_code = 429
    code: ErrorCode = "RATE_LIMITED"

    def __init__(self, retry_after_sec: int) -> None:
        super().__init__()
        self.retry_after_sec = max(1, int(retry_after_sec))


def error_body(code: ErrorCode, details: Sequence[dict[str, str]] = ()) -> dict[str, Any]:
    return {"error": {"code": code, "message": ERROR_MESSAGES[code], "details": list(details)}}


def _error_response(
    status_code: int,
    code: ErrorCode,
    details: Sequence[dict[str, str]] = (),
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=error_body(code, details), headers=headers)


def _field_name(loc: Sequence[Any]) -> str:
    # loc 예: ("query", "deadline"), ("path", "stationId"), ("body", "text"), ("body",)
    names = [str(part) for part in loc if not isinstance(part, int)]
    if len(names) > 1:
        return names[-1]
    return names[0] if names else "request"


def _reason(field: str, error_type: str) -> str:
    if error_type in _REASON_BY_ERROR_TYPE:
        return _REASON_BY_ERROR_TYPE[error_type]
    if error_type in _FORMAT_ERROR_TYPES:
        return _FORMAT_REASON_BY_FIELD.get(field, _DEFAULT_REASON)
    return _DEFAULT_REASON


def validation_details(errors: Sequence[dict[str, Any]]) -> list[dict[str, str]]:
    """pydantic 오류 목록 → [{field, reason}]. 입력값·원래 메시지는 버린다. 같은 필드는 한 번만."""
    details: list[dict[str, str]] = []
    seen: set[str] = set()
    for error in errors:
        field = _field_name(error.get("loc", ()))
        if field in seen:
            continue
        seen.add(field)
        details.append({"field": field, "reason": _reason(field, str(error.get("type", "")))})
    return details


async def _handle_app_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, AppError)
    headers = None
    if isinstance(exc, RateLimitedError):
        headers = {"Retry-After": str(exc.retry_after_sec)}
    return _error_response(exc.status_code, exc.code, exc.details, headers)


async def _handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, RequestValidationError)
    return _error_response(400, "VALIDATION_FAILED", validation_details(exc.errors()))


async def _handle_http_error(request: Request, exc: Exception) -> JSONResponse:
    assert isinstance(exc, StarletteHTTPException)
    if exc.status_code == 404:
        return _error_response(404, "NOT_FOUND")
    if exc.status_code == 405:
        allow = (exc.headers or {}).get("Allow")
        return _error_response(
            405, "METHOD_NOT_ALLOWED", headers={"Allow": allow} if allow else None
        )
    if exc.status_code >= 500:
        logger.error("api_http_error status=%d path=%s", exc.status_code, request.url.path)
        return _error_response(500, "INTERNAL_ERROR")
    # 계약에 없는 4xx(예: 본문 형식 오류)는 검증 실패로 통일한다.
    return _error_response(400, "VALIDATION_FAILED")


async def _handle_unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    # error 로그에는 예외 종류와 경로만 남긴다. str(exc)·추적에는 입력값·비밀값이 섞일 수
    # 있어서다. 추적은 디버깅용으로 debug 레벨에서만 남긴다(로그 필터가 비밀값을 가린다).
    logger.error(
        "api_internal_error method=%s path=%s error=%s",
        request.method,
        request.url.path,
        type(exc).__name__,
    )
    logger.debug("api_internal_error_traceback path=%s", request.url.path, exc_info=exc)
    return _error_response(500, "INTERNAL_ERROR")


async def _catch_unexpected_errors(request: Request, call_next):
    try:
        return await call_next(request)
    except Exception as exc:
        return await _handle_unexpected_error(request, exc)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(AppError, _handle_app_error)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, _handle_http_error)
    # add_exception_handler(Exception) 로 두면 Starlette 가 응답 뒤 예외를 다시 던져 uvicorn 이
    # 'uvicorn.error' 에 추적을 ERROR 로 남긴다. 미들웨어에서 잡아 다시 던지지 않게 한다.
    app.middleware("http")(_catch_unexpected_errors)
