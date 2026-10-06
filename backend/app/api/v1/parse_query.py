"""POST /parse-query(계약 4.6절)."""

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import get_parse_query_service, limit_parse_query
from app.api.responses import error_responses
from app.schemas.parse_query import ParseQueryRequest, ParseQueryResponse
from app.services.parse_query import ParseQueryService

router = APIRouter(tags=["parse-query"])


@router.post(
    "/parse-query",
    response_model=ParseQueryResponse,
    responses=error_responses(400, 429, 500),
    dependencies=[Depends(limit_parse_query)],
    summary="자연어 질문 → 조회 조건(뼈대 단계: 항상 unavailable)",
)
def parse_query(
    body: ParseQueryRequest,
    service: Annotated[ParseQueryService, Depends(get_parse_query_service)],
) -> ParseQueryResponse:
    """text 는 앞뒤 공백을 뺀 1–200자. 빈 문자열·초과면 400. 원문은 저장·로그하지 않는다."""
    return service.parse(body.text)
