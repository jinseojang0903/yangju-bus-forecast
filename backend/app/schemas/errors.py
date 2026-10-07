"""공통 에러 형식(계약 1.4절)."""

from app.schemas.common import ApiModel, ErrorCode


class ErrorDetail(ApiModel):
    field: str
    reason: str


class ErrorInfo(ApiModel):
    code: ErrorCode
    message: str
    details: list[ErrorDetail]


class ErrorResponse(ApiModel):
    error: ErrorInfo
