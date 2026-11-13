"""사례 검색(CLAUDE.md '바꾸지 않는 규칙 > 사례 검색과 확률').

숫자는 모두 rules(settings.ForecastRules)에서 읽는다. 이 모듈에 규칙 값을 다시 쓰지 않는다.
보정·평활은 하지 않는다. 확률은 CaseSearchResult.no_seat_probability(k ÷ n) 그대로다.

앞차 간격은 분(float)으로 들어오지만 비교는 정수 밀리초로, 거리는 분수(Fraction)로 정확히 한다.
분 단위 float 의 차는 오차가 생긴다(예: 310초와 130초는 정확히 180초 차인데
310/60 − 130/60 = 3.0000000000000004 라 ±3분 허용폭에서 빠진다). 수집 시각은 밀리초까지 있고
DB 의 headway_min 은 소수 둘째 자리(0.6초 단위)라 밀리초로 반올림하면 원래 값으로 돌아온다.
"""

from collections.abc import Sequence
from datetime import datetime
from fractions import Fraction

from app.core.settings import ForecastRules
from app.forecast.interfaces import CaseFeature, CaseSearchResult, LabeledCase

_MILLISECONDS_PER_MINUTE = 60_000

# 정렬 키: (거리, −확정 시각, trip_key). 가까운 순, 거리가 같으면 최근 운행 우선.
# trip_key 는 거리와 확정 시각이 모두 같을 때 결과가 입력 순서에 좌우되지 않게 붙인다.
_SortKey = tuple[Fraction, float, str]


def _headway_ms(headway_min: float) -> int:
    """앞차 간격(분) → 정수 밀리초. 분 단위 float 의 표현 오차를 없앤다."""
    return round(headway_min * _MILLISECONDS_PER_MINUTE)


def _sort_key_if_similar(
    case: LabeledCase,
    *,
    feature: CaseFeature,
    reference_at: datetime,
    seats: int,
    headway_ms: int,
    rules: ForecastRules,
) -> _SortKey | None:
    """case 가 필터와 허용폭을 모두 지나면 정렬 키, 아니면 None."""
    candidate = case.feature
    if case.label is None or case.confirmed_at is None:
        return None
    if not candidate.computable or candidate.seats is None or candidate.headway_min is None:
        return None
    if (candidate.route_id, candidate.station_id, candidate.lead_time_min) != (
        feature.route_id,
        feature.station_id,
        feature.lead_time_min,
    ):
        return None
    # 예보 시점 전에 정답이 확정된 운행만 쓴다. 같은 시각은 '전'이 아니다.
    # 자기 운행편은 도착(확정)이 기준 시각보다 늦으므로 여기서 함께 빠진다.
    if not case.confirmed_at < reference_at:
        return None
    seat_diff = abs(candidate.seats - seats)
    headway_diff_ms = abs(_headway_ms(candidate.headway_min) - headway_ms)
    headway_tolerance_ms = rules.headway_tolerance_min * _MILLISECONDS_PER_MINUTE
    if seat_diff > rules.seat_tolerance or headway_diff_ms > headway_tolerance_ms:
        return None
    # 분수로 계산해 '거리가 같다'가 float 오차로 깨지지 않게 한다.
    distance = Fraction(seat_diff, rules.seat_distance_divisor) + Fraction(
        headway_diff_ms, rules.headway_distance_divisor * _MILLISECONDS_PER_MINUTE
    )
    return distance, -case.confirmed_at.timestamp(), candidate.trip_key


def search_cases(
    feature: CaseFeature,
    history: Sequence[LabeledCase],
    rules: ForecastRules,
) -> CaseSearchResult:
    """feature 와 비슷한 지난 사례를 찾아 n(사례 수)·k(그중 도착 상태 0석)를 센다.

    입력
    - feature: 예보할 운행편의 특징(CaseFeature)
    - history: 사례 후보. 시운전·공휴일 운행과 쓰지 않을 정답 등급은 호출하는 쪽이 미리 뺀다.
      시점 필터는 이 함수가 하므로 예보 시점 뒤의 운행이 섞여 있어도 된다.
    - rules: settings.ForecastRules

    규칙
    - 필터: 노선·목표 정류장·선행시간 일치, 정답 미확인(label None) 제외, 특징을 계산하지 못한
      사례 제외, confirmed_at < feature.reference_at(같은 날 앞선 운행 포함)
    - 허용폭(경계 포함): |잔여석 차| ≤ rules.seat_tolerance,
      |간격 차| ≤ rules.headway_tolerance_min
    - 거리: |잔여석 차| ÷ rules.seat_distance_divisor
      + |간격 차| ÷ rules.headway_distance_divisor
    - 선택: 가까운 순 최대 rules.max_cases 건, 운행당(trip_key) 1건,
      거리가 같으면 최근 운행(confirmed_at 이 늦은 것) 우선

    출력: CaseSearchResult(n, k, case_trip_keys(가까운 순), status)
    - feature 를 계산하지 못했으면(computable False) status missing_input, n = k = 0
    - n < rules.min_cases_to_show 이면 status insufficient_cases(n·k 는 찾은 값 그대로)
    - 그 밖은 status ok
    """
    if (
        not feature.computable
        or feature.reference_at is None
        or feature.seats is None
        or feature.headway_min is None
    ):
        return CaseSearchResult(n=0, k=0, case_trip_keys=(), status="missing_input")

    headway_ms = _headway_ms(feature.headway_min)
    similar: list[tuple[_SortKey, LabeledCase]] = []
    for case in history:
        sort_key = _sort_key_if_similar(
            case,
            feature=feature,
            reference_at=feature.reference_at,
            seats=feature.seats,
            headway_ms=headway_ms,
            rules=rules,
        )
        if sort_key is not None:
            similar.append((sort_key, case))
    similar.sort(key=lambda entry: entry[0])

    selected: list[LabeledCase] = []
    seen_trip_keys: set[str] = set()
    for _, case in similar:
        if case.feature.trip_key in seen_trip_keys:
            continue
        seen_trip_keys.add(case.feature.trip_key)
        selected.append(case)
        if len(selected) >= rules.max_cases:
            break

    n = len(selected)
    k = sum(1 for case in selected if case.label == 1)
    return CaseSearchResult(
        n=n,
        k=k,
        case_trip_keys=tuple(case.feature.trip_key for case in selected),
        status="ok" if n >= rules.min_cases_to_show else "insufficient_cases",
    )
