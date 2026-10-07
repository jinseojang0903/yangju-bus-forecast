"""0석 확률과 위험 등급(계약 2장 RiskLevel·4.4절, CLAUDE.md '사례 검색과 확률')."""

from app.core.settings import FORECAST_RULES, NO_SEAT_PROBABILITY_DECIMALS, ForecastRules
from app.schemas.common import RiskLevel


def risk_level(probability: float, rules: ForecastRules = FORECAST_RULES) -> RiskLevel:
    """확률 p(0–1)의 위험 등급. p ≥ 0.7 이면 high, p < 0.3 이면 low, 그 사이는 medium.

    경계값은 settings.FORECAST_RULES(risk_high_min, risk_low_below)를 쓴다.
    반올림 전 k/n 을 넘긴다. 0–1 밖의 값이면 ValueError.
    """
    if not 0.0 <= probability <= 1.0:
        raise ValueError("확률은 0 이상 1 이하여야 한다")
    if probability >= rules.risk_high_min:
        return "high"
    if probability < rules.risk_low_below:
        return "low"
    return "medium"


def no_seat_probability(k: int, n: int) -> tuple[float, RiskLevel]:
    """(API noSeatProbability, riskLevel).

    확률은 k/n(보정 없음)을 NO_SEAT_PROBABILITY_DECIMALS 자리로 반올림한 값(DB numeric(5,4)
    와 같다). 위험 등급은 반올림 전 k/n 으로 정한다. n ≤ 0 이거나 k 가 0..n 밖이면 ValueError.
    """
    if n <= 0 or not 0 <= k <= n:
        raise ValueError("0 ≤ k ≤ n, n > 0 이어야 한다")
    exact = k / n
    return round(exact, NO_SEAT_PROBABILITY_DECIMALS), risk_level(exact)


RISK_RANK: dict[RiskLevel, int] = {"low": 0, "medium": 1, "high": 2}
