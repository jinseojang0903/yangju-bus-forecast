"""요청 본문 크기 상한(ASGI 미들웨어).

- Content-Length 가 상한을 넘거나 숫자가 아니면 본문을 읽기 전에 400 VALIDATION_FAILED.
- Content-Length 없이(청크 전송) 들어오면 읽으면서 세고, 상한을 넘는 순간 읽기를 멈추고 400.
"""

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.errors import error_body
from app.core.settings import MAX_REQUEST_BODY_BYTES

_TOO_LARGE_DETAILS = [{"field": "body", "reason": "본문이 너무 큽니다"}]


class BodyTooLargeError(Exception):
    """스트리밍 중 상한을 넘었다. 미들웨어가 잡아 400 으로 바꾼다."""


class BodySizeLimitMiddleware:
    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        declared = _content_length(scope)
        if declared is not None and (declared < 0 or declared > self.max_bytes):
            await _send_too_large(send)
            return

        received = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise BodyTooLargeError
            return message

        async def tracking_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracking_send)
        except BodyTooLargeError:
            # FastAPI 가 본문 읽기 오류를 400 으로 바꾸지 않고 올려 보낸 경우.
            if not response_started:
                await _send_too_large(send)


def _content_length(scope: Scope) -> int | None:
    for name, value in scope.get("headers", []):
        if name == b"content-length":
            try:
                return int(value)
            except ValueError:
                return -1  # 숫자가 아니면 형식 오류로 본다
    return None


async def _send_too_large(send: Send) -> None:
    payload: dict[str, Any] = error_body("VALIDATION_FAILED", _TOO_LARGE_DETAILS)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": 400,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})
