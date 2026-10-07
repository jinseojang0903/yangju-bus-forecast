"""OpenAPI 에 보일 공통 에러 응답(계약 1.4절)."""

from typing import Any

from app.schemas.errors import ErrorResponse

_DESCRIPTIONS: dict[int, str] = {
    400: "VALIDATION_FAILED: 요청 형식·필드 검증 실패",
    404: "NOT_FOUND: 경로 또는 리소스 없음",
    429: "RATE_LIMITED: 호출 횟수 제한 초과(Retry-After 헤더, 초)",
    500: "INTERNAL_ERROR: 서버 내부 오류",
}


def error_responses(*status_codes: int) -> dict[int | str, dict[str, Any]]:
    return {
        code: {"model": ErrorResponse, "description": _DESCRIPTIONS[code]} for code in status_codes
    }
