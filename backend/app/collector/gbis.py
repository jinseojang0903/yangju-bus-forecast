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
    GBIS_QUOTA_EXCEEDED_MARKER,
    GBIS_QUOTA_EXCEEDED_REASON_CODES,
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
    # 공공데이터포털 하루 호출량 초과(게이트웨이 오류). 실패이며 별도로 크게 기록한다.
    is_quota_exceeded: bool = False
    # 빈 응답: response 는 있으나 msgHeader·msgBody 가 없다(운행 안 하는 시간의 P 노선 등).
    # 결과 없음과 같이 실패가 아니다(ok=True, is_no_result=True).
    is_empty: bool = False


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
    is_quota_exceeded: bool = False
    is_empty: bool = False


def normalize_result_code(value: Any) -> str | None:
    """결과 코드를 비교용 문자열로. 숫자면 앞의 0 을 뗀다("00" → "0"). 빈 값이면 None."""
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
        reason_code = normalize_result_code(_xml_tag(text, "returnReasonCode"))
        message = auth_message or err_message
        error = f"게이트웨이 오류: {message or '알 수 없음'}"
        if reason_code:
            error += f" (returnReasonCode={reason_code})"
        is_quota_exceeded = (
            GBIS_QUOTA_EXCEEDED_MARKER in text or reason_code in GBIS_QUOTA_EXCEEDED_REASON_CODES
        )
        if is_quota_exceeded:
            error = "하루 호출량 초과. " + error
        return ParsedBody(
            ok=False,
            result_code=reason_code,
            result_message=message,
            error=error,
            body=text,
            is_quota_exceeded=is_quota_exceeded,
        )
    if GBIS_QUOTA_EXCEEDED_MARKER in text:
        return ParsedBody(
            ok=False,
            result_code=None,
            result_message=GBIS_QUOTA_EXCEEDED_MARKER,
            error=f"하루 호출량 초과. {GBIS_QUOTA_EXCEEDED_MARKER}",
            body=text,
            is_quota_exceeded=True,
        )
    if text.lstrip().startswith("<"):
        code = normalize_result_code(_xml_tag(text, "resultCode"))
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


def _is_empty_response(data: dict[str, Any], text: str) -> bool:
    """`{"response": {"comMsgHeader": ""}}` 처럼 response 객체에 msgHeader·msgBody 가 없으면 True.

    포털 표준 header 가 있거나 게이트웨이 오류 표시가 있으면 빈 응답이 아니다(실패로 남긴다).
    """
    response = data.get("response")
    if not isinstance(response, dict):
        return False
    if any(key in response for key in ("msgHeader", "msgBody", "header")):
        return False
    return not any(marker in text for marker in GBIS_GATEWAY_ERROR_MARKERS)


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
    # GBIS 자체 resultCode 의 22 는 뜻이 다를 수 있어 포털 표준 header 의 22 만 호출량 초과로 본다.
    is_quota_exceeded = GBIS_QUOTA_EXCEEDED_MARKER in text
    header = response.get("msgHeader")
    if isinstance(header, dict):
        code = normalize_result_code(header.get("resultCode"))
        message = _as_text(header.get("resultMessage"))
    else:
        # 공공데이터포털 표준 형식(header.resultCode/resultMsg)으로 오는 경우 대비
        alt_header = response.get("header")
        if isinstance(alt_header, dict):
            code = normalize_result_code(alt_header.get("resultCode"))
            message = _as_text(alt_header.get("resultMsg") or alt_header.get("resultMessage"))
            is_quota_exceeded = is_quota_exceeded or code in GBIS_QUOTA_EXCEEDED_REASON_CODES

    if is_quota_exceeded:
        return ParsedBody(
            ok=False,
            result_code=code,
            result_message=message,
            error=f"하루 호출량 초과. resultCode={code} {message or ''}".strip(),
            body=data,
            is_quota_exceeded=True,
        )
    if _is_empty_response(data, text):
        return ParsedBody(True, None, None, None, data, [], is_no_result=True, is_empty=True)
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
        *,
        timeout_sec: float = GBIS_HTTP_TIMEOUT_SEC,
    ) -> None:
        """timeout_sec 는 연결·읽기·쓰기·연결 풀 대기 각각에 적용된다(httpx.Timeout)."""
        self._service_key = service_key
        self._redactor = redactor
        self._http = http or httpx.Client(timeout=httpx.Timeout(timeout_sec))

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
            is_quota_exceeded=parsed.is_quota_exceeded,
            is_empty=parsed.is_empty,
        )


def _elapsed_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)
