"""GBIS(공공데이터포털 경기도 v2) 호출과 관대한 응답 해석.

원본을 그대로 남기는 것이 우선이다. 해석은 성공/실패 판정과 요약에만 쓴다.
응답 구조 가정: response.msgHeader.resultCode/resultMessage, response.msgBody.<목록>.
"""

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.collector.redact import Redactor, describe_exception
from app.core.settings import (
    GBIS_GATEWAY_ERROR_MARKERS,
    GBIS_HTTP_TIMEOUT_SEC,
    GBIS_NO_RESULT_CODES,
    GBIS_SUCCESS_CODES,
    GbisEndpoint,
)

SERVICE_KEY_PARAM = "serviceKey"


@dataclass(frozen=True)
class ParsedBody:
    ok: bool
    result_code: str | None
    result_message: str | None
    error: str | None
    body: Any  # JSON 이면 객체, 아니면 원문 문자열
    items: list[dict[str, Any]] = field(default_factory=list)
    is_no_result: bool = False


@dataclass(frozen=True)
class CallResult:
    api: str
    service: str
    params: dict[str, str]  # serviceKey 를 뺀 요청 파라미터
    http_status: int | None
    elapsed_ms: int
    ok: bool
    result_code: str | None
    result_message: str | None
    error: str | None  # 비밀값을 가린 문자열
    body: Any
    items: list[dict[str, Any]] = field(default_factory=list)
    is_no_result: bool = False


def _normalize_code(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return str(int(text)) if text.isdigit() else text


def _as_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def as_list(value: Any) -> list[dict[str, Any]]:
    """목록이 1건이면 dict 로 오기도 한다. 항상 dict 의 리스트로 돌려준다."""
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [v for v in value if isinstance(v, dict)]
    return []


def _extract_items(msg_body: Any, list_key: str) -> list[dict[str, Any]]:
    if not isinstance(msg_body, dict):
        return []
    value = msg_body.get(list_key)
    if value is None:
        # 목록 이름이 가정과 다를 때도 첫 목록/객체를 쓴다.
        # 실제 이름은 save-fixture 로 확인한다.
        value = next((v for v in msg_body.values() if isinstance(v, (list, dict))), None)
    return as_list(value)


def _xml_tag(text: str, tag: str) -> str | None:
    match = re.search(rf"<{tag}>(.*?)</{tag}>", text, re.DOTALL)
    return match.group(1).strip() if match else None


def _parse_non_json(text: str) -> ParsedBody:
    if any(marker in text for marker in GBIS_GATEWAY_ERROR_MARKERS):
        auth_message = _xml_tag(text, "returnAuthMsg")
        err_message = _xml_tag(text, "errMsg")
        reason_code = _xml_tag(text, "returnReasonCode")
        message = auth_message or err_message
        error = f"게이트웨이 오류: {message or '알 수 없음'}"
        if reason_code:
            error += f" (returnReasonCode={reason_code})"
        return ParsedBody(
            ok=False,
            result_code=_normalize_code(reason_code),
            result_message=message,
            error=error,
            body=text,
        )
    if text.lstrip().startswith("<"):
        code = _normalize_code(_xml_tag(text, "resultCode"))
        message = _xml_tag(text, "resultMessage") or _xml_tag(text, "resultMsg")
        return ParsedBody(
            ok=False,
            result_code=code,
            result_message=message,
            error="JSON 이 아닌 XML 응답",
            body=text,
        )
    return ParsedBody(
        ok=False, result_code=None, result_message=None, error="JSON 이 아닌 응답", body=text
    )


def parse_body(text: str, list_key: str) -> ParsedBody:
    """응답 본문을 해석한다. 결과 없음 코드는 실패가 아니다(ok=True, items=[])."""
    try:
        data = json.loads(text.lstrip("﻿"))
    except ValueError:
        return _parse_non_json(text)

    if not isinstance(data, dict):
        return ParsedBody(
            ok=False,
            result_code=None,
            result_message=None,
            error="예상하지 못한 JSON 구조",
            body=data,
        )

    response = data.get("response") if isinstance(data.get("response"), dict) else data
    code: str | None = None
    message: str | None = None
    header = response.get("msgHeader")
    if isinstance(header, dict):
        code = _normalize_code(header.get("resultCode"))
        message = _as_text(header.get("resultMessage"))
    else:
        # 공공데이터포털 표준 형식(header.resultCode/resultMsg)으로 오는 경우 대비
        alt_header = response.get("header")
        if isinstance(alt_header, dict):
            code = _normalize_code(alt_header.get("resultCode"))
            message = _as_text(alt_header.get("resultMsg") or alt_header.get("resultMessage"))

    items = _extract_items(response.get("msgBody"), list_key)
    if code is None:
        return ParsedBody(
            ok=False,
            result_code=None,
            result_message=message,
            error="resultCode 없음",
            body=data,
            items=items,
        )
    if code in GBIS_SUCCESS_CODES:
        return ParsedBody(True, code, message, None, data, items)
    if code in GBIS_NO_RESULT_CODES:
        return ParsedBody(True, code, message, None, data, [], is_no_result=True)
    return ParsedBody(
        ok=False,
        result_code=code,
        result_message=message,
        error=f"resultCode={code} {message or ''}".strip(),
        body=data,
        items=items,
    )


class GbisClient:
    """GBIS 호출. 서비스 키는 요청에만 싣고, 결과(CallResult)에는 남기지 않는다."""

    def __init__(
        self,
        service_key: str,
        redactor: Redactor,
        http: httpx.Client | None = None,
    ) -> None:
        self._service_key = service_key
        self._redactor = redactor
        self._http = http or httpx.Client(timeout=GBIS_HTTP_TIMEOUT_SEC)

    @property
    def redactor(self) -> Redactor:
        return self._redactor

    def close(self) -> None:
        self._http.close()

    def call(self, endpoint: GbisEndpoint, params: Mapping[str, str]) -> CallResult:
        safe_params = {
            k: str(v) for k, v in params.items() if k.lower() != SERVICE_KEY_PARAM.lower()
        }
        safe_params["format"] = "json"
        request_params = {**safe_params, SERVICE_KEY_PARAM: self._service_key}

        started = time.perf_counter()
        try:
            response = self._http.get(endpoint.url, params=request_params)
        except httpx.HTTPError as exc:
            return CallResult(
                api=endpoint.api,
                service=endpoint.service,
                params=safe_params,
                http_status=None,
                elapsed_ms=_elapsed_ms(started),
                ok=False,
                result_code=None,
                result_message=None,
                error=describe_exception(exc, self._redactor),
                body=None,
            )
        elapsed_ms = _elapsed_ms(started)

        parsed = parse_body(response.text, endpoint.list_key)
        ok = parsed.ok
        error = parsed.error
        if response.status_code != 200:
            ok = False
            error = f"HTTP {response.status_code}" + (f"; {error}" if error else "")
        return CallResult(
            api=endpoint.api,
            service=endpoint.service,
            params=safe_params,
            http_status=response.status_code,
            elapsed_ms=elapsed_ms,
            ok=ok,
            result_code=parsed.result_code,
            result_message=self._redactor.redact_optional(parsed.result_message),
            error=self._redactor.redact_optional(error),
            body=parsed.body,
            items=parsed.items,
            is_no_result=parsed.is_no_result,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
