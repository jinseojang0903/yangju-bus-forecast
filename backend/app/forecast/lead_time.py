"""화면에 쓸 선행시간 선택(계약 2.1절)."""

from collections.abc import Iterable
from datetime import datetime
from typing import Protocol, cast

from app.core.settings import LEAD_TIME_SELECTION_RULE
from app.schemas.common import LeadTimeMin


class IssuableForecast(Protocol):
    """선행시간 하나의 예보. API 의 Forecast 모델도 이 모양을 만족한다."""

    @property
    def lead_time_min(self) -> int: ...

    @property
    def issued_at(self) -> datetime | None: ...


def select_lead_time(
    forecasts: Iterable[IssuableForecast],
    now: datetime,
    rule: str = LEAD_TIME_SELECTION_RULE,
) -> LeadTimeMin | None:
    """버스 한 대의 예보 중 화면에 쓸 선행시간. 하나도 고를 수 없으면 None.

    rule="latest_issued": issued_at 이 있고 now 이전인 예보 중 issued_at 이 가장 늦은 것.
    예: 도착 12분 전이면 15분 예보, 7분 전이면 10분 예보.
    같은 시각이면 선행시간이 짧은 쪽(도착에 더 가까운 예보)을 고른다.
    알 수 없는 rule 이면 ValueError. now 는 시간대가 있어야 한다.
    """
    if rule != "latest_issued":
        raise ValueError(f"알 수 없는 선행시간 선택 규칙: {rule}")
    issued = [f for f in forecasts if f.issued_at is not None and f.issued_at <= now]
    if not issued:
        return None
    chosen = max(issued, key=lambda f: (f.issued_at, -f.lead_time_min))
    return cast(LeadTimeMin, chosen.lead_time_min)
