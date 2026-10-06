"""자연어 질문 → 조회 조건(계약 4.6절, F08). 뼈대 단계에서는 항상 unavailable.

질문 원문은 저장·로그하지 않는다.
"""

from app.schemas.parse_query import ParsedQuery, ParseQueryResponse

UNAVAILABLE_MESSAGE = "지금은 질문으로 조회할 수 없어요. 정류장과 목적지를 직접 골라 주세요."


class ParseQueryService:
    def parse(self, text: str) -> ParseQueryResponse:
        # TODO(backend-dev/2026-10-06): F08 LLM 연결 때 text 를 쓴다. 지금은 LLM 을 끈 상태다.
        del text
        return ParseQueryResponse(
            status="unavailable",
            fallback_reason="llm_disabled",
            query=ParsedQuery(station=None, destination=None, deadline=None),
            message=UNAVAILABLE_MESSAGE,
        )
