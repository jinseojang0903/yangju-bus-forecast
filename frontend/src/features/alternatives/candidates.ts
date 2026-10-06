import type { AlternativeCandidate, Bus, Forecast, RecommendedAlternative } from "../../api/types";

/** 서버가 추천한 후보인지(노선·차량이 모두 같아야 한다) */
export function isRecommendedCandidate(
  candidate: AlternativeCandidate,
  recommended: RecommendedAlternative | null,
): boolean {
  return (
    recommended !== null &&
    candidate.routeId === recommended.routeId &&
    candidate.vehicleId === recommended.vehicleId
  );
}

/**
 * 후보가 쓴 선행시간의 예보를 buses 에서 찾는다.
 * 계약의 candidates 에는 preliminary·n·k 가 없어서, '예비' 표기와 'n회 중 k회' 를 붙이려면
 * 같은 버스(노선·출처·차량)의 같은 선행시간 예보를 읽어야 한다. 값을 새로 계산하지는 않는다.
 * vehicleId 가 없는 후보(timetable_next 등)는 같은 차인지 확정할 수 없으므로 찾지 않는다(null).
 * 이때 화면은 n·k 없이 '예비'만 붙인다.
 */
export function findCandidateForecast(
  candidate: AlternativeCandidate,
  buses: Bus[],
): Forecast | null {
  if (candidate.leadTimeMin === null || candidate.vehicleId === null) return null;
  const bus = buses.find(
    (item) =>
      item.routeId === candidate.routeId &&
      item.source === candidate.source &&
      item.vehicleId === candidate.vehicleId,
  );
  return bus?.forecasts.find((forecast) => forecast.leadTimeMin === candidate.leadTimeMin) ?? null;
}
