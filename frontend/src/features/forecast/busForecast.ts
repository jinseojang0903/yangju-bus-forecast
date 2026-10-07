import type {
  Bus,
  Forecast,
  ForecastStatus,
  LeadTimeMin,
  RiskLevel,
  Timestamp,
} from "../../api/types";

/** 서버가 고른 선행시간(selectedLeadTimeMin)의 예보. 없으면 null */
export function findSelectedForecast(bus: Bus): Forecast | null {
  if (bus.selectedLeadTimeMin === null) return null;
  return bus.forecasts.find((forecast) => forecast.leadTimeMin === bus.selectedLeadTimeMin) ?? null;
}

export interface ProbabilityView {
  kind: "probability";
  leadTimeMin: LeadTimeMin;
  issuedAt: Timestamp | null;
  noSeatProbability: number;
  n: number;
  k: number;
  riskLevel: RiskLevel;
  preliminary: boolean;
}

export interface StatusView {
  kind: "status";
  status: Exclude<ForecastStatus, "ok">;
}

export type BusForecastView = ProbabilityView | StatusView;

/**
 * 버스 카드에 확률을 보일지, 상태 안내를 보일지 정한다. 값은 서버 것을 그대로 옮기기만 한다.
 * - 스냅샷 전체가 오래됨(stale) → 정보 오래됨
 * - 고른 선행시간이 없음(아직 어떤 예보 시점도 지나지 않음) → 아직 시점 전
 * - status 가 ok 가 아님 → 그 상태
 * - ok 인데 확률·n·k·등급 중 빠진 값이 있음(계약 위반) → 확률을 지어내지 않고 입력 누락으로 보인다
 */
export function toBusForecastView(bus: Bus, isSnapshotStale: boolean): BusForecastView {
  if (isSnapshotStale) return { kind: "status", status: "stale" };
  const forecast = findSelectedForecast(bus);
  if (!forecast) return { kind: "status", status: "not_yet" };
  if (forecast.status !== "ok") return { kind: "status", status: forecast.status };

  const { noSeatProbability, n, k, riskLevel } = forecast;
  if (noSeatProbability === null || n === null || k === null || riskLevel === null) {
    // 화면은 우회해서 그리되, 개발 중에는 계약 위반을 알 수 있게 남긴다(운영 빌드에서는 제거된다).
    if (import.meta.env.DEV) {
      console.warn("[contract] status ok 인데 확률·n·k·등급 중 null 이 있음", {
        routeId: bus.routeId,
        leadTimeMin: forecast.leadTimeMin,
      });
    }
    return { kind: "status", status: "missing_input" };
  }
  return {
    kind: "probability",
    leadTimeMin: forecast.leadTimeMin,
    issuedAt: forecast.issuedAt,
    noSeatProbability,
    n,
    k,
    riskLevel,
    preliminary: forecast.preliminary,
  };
}

export function busKey(bus: Pick<Bus, "routeId" | "source" | "vehicleId">): string {
  return `${bus.routeId}-${bus.source}-${bus.vehicleId ?? "none"}`;
}
