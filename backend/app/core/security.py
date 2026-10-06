"""토큰 비교 등 보안 도우미."""

import hmac

from app.core.settings import HEALTH_DETAIL_TOKEN_MIN_LENGTH


def is_valid_token(
    provided: str | None,
    configured: str,
    min_length: int = HEALTH_DETAIL_TOKEN_MIN_LENGTH,
) -> bool:
    """provided 가 configured 와 같으면 True(상수 시간 비교).

    configured 가 min_length 자 미만이면(비어 있거나 너무 짧음) 설정되지 않은 것으로 보고
    항상 False. provided 가 None·빈 값이어도 False.
    """
    if len(configured) < min_length or not provided:
        return False
    return hmac.compare_digest(provided.encode("utf-8"), configured.encode("utf-8"))
