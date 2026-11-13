import { isApiError } from "../../api/client";
import type { HealthResponse } from "../../api/types";
import {
  COLLECTION_NO_RECORD,
  COLLECTION_STATUS_CHECK_DELAYED,
  COLLECTION_STATUS_SEPARATOR,
  COLLECTION_STATUS_TITLE,
  COLLECTION_STATUS_UNAVAILABLE,
  elapsedAgoText,
  lastCollectedAtText,
  lastUpdatedAgoText,
} from "../../lib/labels";
import {
  formatKstHourMinute,
  formatKstMonthDayHourMinute,
  isSameKstDate,
  toElapsed,
} from "../../lib/time";

/**
 * 수집 상태 재조회 간격. '나머지 GET' 호출 제한(분당 60회, 계약 1.1절)에 비해 충분히 여유가 있고,
 * 수집 주기(10~40초)와 비슷해 '살아 있는지'를 보이기에 충분하다.
 */
export const HEALTH_REFETCH_INTERVAL_MS = 30_000;

/** 경과 시간 표시를 다시 그리는 간격. 표시는 분 단위('방금' 포함)라 초마다 그릴 필요가 없다 */
export const CLIENT_CLOCK_TICK_MS = 15_000;

/**
 * 서버가 ok 라고 해도 마지막 갱신이 이보다 오래되면 주의 표시로 낮춘다.
 * 응답을 못 받는 동안 기기 경과가 쌓여 '수집 정상'이 낡은 채 남는 것을 막는다.
 * 서버의 degraded 판정(대상 주기의 3배 초과, 계약 4.1)보다 넉넉하게 잡은 화면 쪽 안전장치다.
 */
export const OK_STALE_AFTER_SEC = 180;

const KNOWN_STATUSES: readonly HealthResponse["status"][] = ["ok", "degraded", "idle"];

function isKnownStatus(status: unknown): status is HealthResponse["status"] {
  return KNOWN_STATUSES.includes(status as HealthResponse["status"]);
}

/** 429 면 Retry-After 를 따르되 평소 간격보다 짧게 부르지 않는다. 그 밖의 실패는 평소 간격 그대로 */
export function healthRefetchDelayMs(error: unknown): number {
  if (isApiError(error) && error.code === "RATE_LIMITED" && error.retryAfterSec !== null) {
    return Math.max(HEALTH_REFETCH_INTERVAL_MS, error.retryAfterSec * 1000);
  }
  return HEALTH_REFETCH_INTERVAL_MS;
}

/**
 * 마지막 수집 성공 뒤 지난 초. 이용자 기기 시계가 틀려도 대략 맞도록
 * 응답 시점의 차이는 서버 시각(now - lastSuccessAt)으로 정하고,
 * 그 뒤 경과만 기기 시계(clientNowMs - receivedAtMs)로 더한다.
 * receivedAtMs 는 응답을 받은 때라 서버가 now 를 찍은 뒤의 전송 지연만큼 짧게 잡히지만,
 * 표시가 분 단위라 무시한다.
 * lastSuccessAt 이 없거나 시각을 해석할 수 없으면 null.
 */
export function lastSuccessAgeSec(
  health: HealthResponse,
  receivedAtMs: number,
  clientNowMs: number,
): number | null {
  if (health.lastSuccessAt === null) return null;
  const serverNowMs = Date.parse(health.now);
  const lastSuccessMs = Date.parse(health.lastSuccessAt);
  if (Number.isNaN(serverNowMs) || Number.isNaN(lastSuccessMs)) return null;
  const sinceResponseMs = Math.max(0, clientNowMs - receivedAtMs);
  return Math.max(0, Math.floor((serverNowMs - lastSuccessMs + sinceResponseMs) / 1000));
}

/** "10:14", 서버 기준 오늘이 아니면 "10/07 10:14" */
export function formatLastCollectedAt(health: HealthResponse): string | null {
  if (health.lastSuccessAt === null) return null;
  return isSameKstDate(health.lastSuccessAt, health.now)
    ? formatKstHourMinute(health.lastSuccessAt)
    : formatKstMonthDayHourMinute(health.lastSuccessAt);
}

/** 색 외에 기호와 문구로도 구분한다 */
export type CollectionStatusTone = "ok" | "warning" | "idle" | "unknown";

export interface CollectionStatusView {
  tone: CollectionStatusTone;
  /** 상태 이름. 바뀔 때만 낭독된다 */
  title: string;
  /** 경과 시간·마지막 수집 시각·확인 지연. 없으면 null */
  detail: string | null;
}

/** 응답을 받지 못했거나 알 수 없는 응답일 때 */
export const UNAVAILABLE_VIEW: CollectionStatusView = {
  tone: "unknown",
  title: COLLECTION_STATUS_UNAVAILABLE,
  detail: null,
};

export interface CollectionStatusInput {
  health: HealthResponse;
  /** 응답을 받은 기기 시각(TanStack dataUpdatedAt) */
  receivedAtMs: number;
  clientNowMs: number;
  /** 이전 응답은 있는데 다시 부르기가 실패하고 있음(TanStack isRefetchError) */
  isRefreshFailing: boolean;
}

function joinDetail(parts: Array<string | null>): string | null {
  const present = parts.filter((part): part is string => part !== null);
  return present.length > 0 ? present.join(COLLECTION_STATUS_SEPARATOR) : null;
}

/** 서버 응답만으로 정한 표시. 확인 지연 표시는 toCollectionStatusView 가 덧붙인다 */
function viewFromHealth(
  health: HealthResponse,
  receivedAtMs: number,
  clientNowMs: number,
): CollectionStatusView {
  if (health.status === "idle") {
    const lastCollectedAt = formatLastCollectedAt(health);
    return {
      tone: "idle",
      title: COLLECTION_STATUS_TITLE.idle,
      detail:
        lastCollectedAt === null ? COLLECTION_NO_RECORD : lastCollectedAtText(lastCollectedAt),
    };
  }
  if (health.lastSuccessAt === null) {
    return { tone: "warning", title: COLLECTION_NO_RECORD, detail: null };
  }
  const ageSec = lastSuccessAgeSec(health, receivedAtMs, clientNowMs);
  const isOkAndFresh = health.status === "ok" && (ageSec === null || ageSec <= OK_STALE_AFTER_SEC);
  return {
    tone: isOkAndFresh ? "ok" : "warning",
    title: COLLECTION_STATUS_TITLE[health.status],
    detail: ageSec === null ? null : lastUpdatedAgoText(elapsedAgoText(toElapsed(ageSec))),
  };
}

/**
 * 응답 하나를 표시 줄로 바꾼다.
 * - 계약에 없는 status 는 '확인할 수 없어요'로 본다
 * - ok/degraded 인데 수집 기록이 없으면 '수집 기록 없음'을 주의 표시로 보인다
 * - ok 라도 기기 경과를 더한 마지막 갱신이 OK_STALE_AFTER_SEC 를 넘으면 주의 표시
 * - idle 은 수집 기록이 없으면 그 사실을 뒤에 붙인다
 * - 다시 부르기가 실패하는 중이면 상태 이름은 두고 '상태 확인 지연'을 붙여 주의 표시
 */
export function toCollectionStatusView(input: CollectionStatusInput): CollectionStatusView {
  const { health, receivedAtMs, clientNowMs, isRefreshFailing } = input;
  if (!isKnownStatus(health.status)) return UNAVAILABLE_VIEW;
  const view = viewFromHealth(health, receivedAtMs, clientNowMs);
  if (!isRefreshFailing) return view;
  return {
    tone: "warning",
    title: view.title,
    detail: joinDetail([view.detail, COLLECTION_STATUS_CHECK_DELAYED]),
  };
}
