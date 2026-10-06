import { isApiError } from "../../api/client";
import type { Timestamp } from "../../api/types";

/**
 * 최소 간격. 서버 스냅샷 캐시(10초, 계약 1.3절)보다 자주 불러도 같은 값이고,
 * 같은 IP 에서 여러 탭을 열어도 호출 제한(분당 30회, 1.1절) 안에 들게 한다.
 */
export const MIN_REFRESH_DELAY_MS = 10_000;
/** 서비스 시간 밖에는 nextRefreshAt 이 다음 날일 수 있다. 기기 절전 뒤 어긋남을 줄이려고 상한을 둔다. */
export const MAX_REFRESH_DELAY_MS = 30 * 60_000;
/** 갱신이 실패했을 때 다시 시도하는 간격 */
export const ERROR_REFRESH_DELAY_MS = 15_000;

function clampDelay(ms: number): number {
  return Math.min(Math.max(ms, MIN_REFRESH_DELAY_MS), MAX_REFRESH_DELAY_MS);
}

export interface RefreshTiming {
  nextRefreshAt: Timestamp;
  computedAt: Timestamp;
}

/**
 * 다음 자동 조회까지 기다릴 시간(ms). 계약 1.3절의 nextRefreshAt 에 맞춘다.
 * 휴대폰 시계는 앞서거나 늦을 수 있으므로 기기 시각(Date.now)을 쓰지 않고,
 * 서버가 준 두 시각의 간격(nextRefreshAt - computedAt)만 쓴다. 응답을 받은 때부터 이 간격을 센다.
 */
export function snapshotRefreshDelayMs(timing: RefreshTiming): number {
  const nextMs = Date.parse(timing.nextRefreshAt);
  const computedMs = Date.parse(timing.computedAt);
  if (Number.isNaN(nextMs) || Number.isNaN(computedMs)) return ERROR_REFRESH_DELAY_MS;
  return clampDelay(nextMs - computedMs);
}

/**
 * 실패 뒤 자동 재조회 간격. false 면 자동으로 다시 부르지 않는다.
 * - 400·404: 조건이 잘못됐으므로 다시 불러도 같다
 * - 429: Retry-After 를 따른다
 */
export function errorRefreshDelayMs(error: unknown): number | false {
  if (!isApiError(error)) return ERROR_REFRESH_DELAY_MS;
  if (error.code === "VALIDATION_FAILED" || error.code === "NOT_FOUND") return false;
  if (error.code === "RATE_LIMITED" && error.retryAfterSec !== null) {
    return clampDelay(error.retryAfterSec * 1000);
  }
  return ERROR_REFRESH_DELAY_MS;
}
