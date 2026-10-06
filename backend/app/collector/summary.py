"""once·save-fixture 출력용 요약. 메인이 실제 응답의 필드 이름을 확인하는 데 쓴다."""

from typing import Any

from app.collector.gbis import CallResult
from app.collector.redact import Redactor

# 라벨·예보에 쓸 필드. 응답에 있는지만 센다.
WATCHED_FIELDS = (
    "stateCd",
    "remainSeatCnt",
    "remainSeatCnt1",
    "remainSeatCnt2",
    "predictTimeSec",
    "predictTimeSec1",
    "predictTimeSec2",
    "predictTime1",
    "predictTime2",
    "stationSeq",
    "stationId",
    "routeId",
)
# 차량(운행편)을 구분할 수 있는 필드 후보
VEHICLE_FIELDS = ("vehId", "vehId1", "vehId2", "plateNo", "plateNo1", "plateNo2")
_MAX_EXAMPLES = 3


def field_presence(items: list[dict[str, Any]]) -> dict[str, str]:
    """필드별 '있는 항목 수/전체'. 하나도 없는 필드도 0/n 으로 보여 준다."""
    total = len(items)
    return {f: f"{sum(1 for it in items if f in it)}/{total}" for f in WATCHED_FIELDS}


def vehicle_examples(items: list[dict[str, Any]]) -> dict[str, list[str]]:
    examples: dict[str, list[str]] = {}
    for name in VEHICLE_FIELDS:
        values = [str(it[name]) for it in items if it.get(name) not in (None, "")]
        if values:
            examples[name] = values[:_MAX_EXAMPLES]
    return examples


def summarize(result: CallResult, label: str, redactor: Redactor) -> list[str]:
    """화면 출력용 줄. 응답 값이 키를 되돌려 주는 경우에 대비해 모든 줄을 가린다."""
    lines = [
        f"[{result.api}] target={label} params={result.params}",
        f"  http={result.http_status} ok={result.ok} code={result.result_code} "
        f"msg={result.result_message} items={len(result.items)} ms={result.elapsed_ms}"
        + (" (결과 없음: 실패 아님)" if result.is_no_result else ""),
    ]
    if result.error:
        lines.append(f"  error={result.error}")
    if result.items:
        presence = " ".join(f"{k}={v}" for k, v in field_presence(result.items).items())
        lines.append(f"  fields: {presence}")
        examples = vehicle_examples(result.items)
        lines.append(f"  vehicle_id_candidates: {examples if examples else '(없음)'}")
        lines.append(f"  first_item_keys: {sorted(result.items[0].keys())}")
    return [redactor.redact(line) for line in lines]
