"""예보 API 앱(docs/api-contract.md v2).

실행: cd backend && uv run uvicorn app.main:app --reload (포트 8000)
문서: /docs (Swagger UI), /openapi.json
"""

import logging
from collections.abc import Callable
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi

from app.api.v1 import api_router
from app.core.api_logging import setup_api_logging
from app.core.body_limit import BodySizeLimitMiddleware
from app.core.errors import register_error_handlers
from app.core.ratelimit import RateLimiter
from app.core.settings import API_PREFIX, Settings, get_settings
from app.repositories.fake import FakeRepository
from app.schemas.snapshot import SnapshotResponse
from app.services.cache import TtlCache

logger = logging.getLogger(__name__)

API_TITLE = "탈 수 있을까? 양주 광역버스 만차 예보 API"
API_VERSION = "0.1.0"
API_DESCRIPTION = (
    "계약: docs/api-contract.md (v2). 모든 4xx/5xx 는 "
    '{"error": {"code", "message", "details"}} 형식이다. '
    "FastAPI 기본 422 는 400 VALIDATION_FAILED 로 바꿔 응답한다."
)


def _openapi_without_422(app: FastAPI) -> Callable[[], dict[str, Any]]:
    """OpenAPI 에서 FastAPI 기본 422 응답을 뺀다(실제 응답은 400, 계약 1.4절)."""

    def openapi() -> dict[str, Any]:
        if app.openapi_schema:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        for path_item in schema.get("paths", {}).values():
            for operation in path_item.values():
                if isinstance(operation, dict):
                    operation.get("responses", {}).pop("422", None)
        component_schemas = schema.get("components", {}).get("schemas", {})
        component_schemas.pop("HTTPValidationError", None)
        component_schemas.pop("ValidationError", None)
        app.openapi_schema = schema
        return schema

    return openapi


def create_app(settings: Settings | None = None) -> FastAPI:
    """앱을 만든다. settings 를 넘기지 않으면 backend/.env 와 환경변수에서 읽는다."""
    settings = settings or get_settings()
    setup_api_logging(settings)
    if settings.fake_data:
        logger.warning("api_fake_data_enabled fake_data=true snapshot=static_scenarios")
    app = FastAPI(title=API_TITLE, version=API_VERSION, description=API_DESCRIPTION)
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()
    app.state.repository = FakeRepository() if settings.fake_data else None
    app.state.snapshot_cache = TtlCache[SnapshotResponse | None]()

    register_error_handlers(app)
    # 나중에 더한 미들웨어가 바깥이다: CORS → 본문 크기 상한 → 라우터.
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type"],
    )
    app.include_router(api_router, prefix=API_PREFIX)
    app.openapi = _openapi_without_422(app)  # type: ignore[method-assign]
    return app


app = create_app()
