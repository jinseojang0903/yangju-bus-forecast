"""POST /parse-query 요청·응답(계약 4.6절)."""

from typing import Annotated, Literal

from pydantic import StringConstraints

from app.schemas.common import QUERY_TEXT_MAX_LENGTH, ApiModel, FallbackReason

# 앞뒤 공백을 뺀 뒤 1–200자. 공백만 있으면 빈 문자열로 보고 400.
QueryText = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=QUERY_TEXT_MAX_LENGTH)
]


class ParseQueryRequest(ApiModel):
    text: QueryText


class ParsedQuery(ApiModel):
    station: str | None
    destination: str | None
    deadline: str | None


class ParseQueryResponse(ApiModel):
    status: Literal["parsed", "need_more", "unavailable"]
    fallback_reason: FallbackReason | None
    query: ParsedQuery
    message: str
