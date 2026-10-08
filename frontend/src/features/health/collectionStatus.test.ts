import { describe, expect, it } from "vitest";
import { ApiError } from "../../api/client";
import type { HealthResponse } from "../../api/types";
import {
  COLLECTION_NO_RECORD,
  COLLECTION_STATUS_TITLE,
  COLLECTION_STATUS_UNAVAILABLE,
} from "../../lib/labels";
import {
  type CollectionStatusInput,
  formatLastCollectedAt,
  HEALTH_REFETCH_INTERVAL_MS,
  healthRefetchDelayMs,
  lastSuccessAgeSec,
  OK_STALE_AFTER_SEC,
  toCollectionStatusView,
} from "./collectionStatus";

const SERVER_NOW = "2026-10-08T07:31:20+09:00";
const RECEIVED_AT = 1_000_000;

function health(
  status: HealthResponse["status"],
  lastSuccessAt: string | null,
  now = SERVER_NOW,
): HealthResponse {
  return { status, now, lastSuccessAt };
}

function input(
  value: HealthResponse,
  overrides: Partial<Omit<CollectionStatusInput, "health">> = {},
): CollectionStatusInput {
  return {
    health: value,
    receivedAtMs: RECEIVED_AT,
    clientNowMs: RECEIVED_AT,
    isRefreshFailing: false,
    ...overrides,
  };
}

describe("lastSuccessAgeSec", () => {
  it("응답 시점은 서버 now 와 lastSuccessAt 차이로 정한다(기기 시계와 무관)", () => {
    // 기기 시계가 서버보다 1시간 빨라도, 응답 직후라면 서버 차이 12초 그대로다
    const receivedAt = Date.parse("2026-10-08T08:31:20+09:00");
    expect(
      lastSuccessAgeSec(health("ok", "2026-10-08T07:31:08+09:00"), receivedAt, receivedAt),
    ).toBe(12);
  });

  it("응답 뒤 경과는 기기 시계로 더한다", () => {
    const value = health("ok", "2026-10-08T07:31:08+09:00");
    expect(lastSuccessAgeSec(value, RECEIVED_AT, RECEIVED_AT + 47_000)).toBe(59);
    expect(lastSuccessAgeSec(value, RECEIVED_AT, RECEIVED_AT + 48_000)).toBe(60);
  });

  it("기기 시계가 응답 시각보다 앞이거나 lastSuccessAt 이 now 보다 뒤여도 음수가 되지 않는다", () => {
    expect(lastSuccessAgeSec(health("ok", "2026-10-08T07:31:08+09:00"), 5_000, 1_000)).toBe(12);
    expect(lastSuccessAgeSec(health("ok", "2026-10-08T07:31:30+09:00"), 0, 0)).toBe(0);
  });

  it("기록이 없거나 시각을 해석할 수 없으면 null", () => {
    expect(lastSuccessAgeSec(health("ok", null), 0, 0)).toBeNull();
    expect(lastSuccessAgeSec(health("ok", "not-a-time"), 0, 0)).toBeNull();
    expect(
      lastSuccessAgeSec(health("ok", "2026-10-08T07:31:08+09:00", "bad-now"), 0, 0),
    ).toBeNull();
  });
});

describe("formatLastCollectedAt", () => {
  it("서버 기준 오늘이면 HH:MM", () => {
    expect(
      formatLastCollectedAt(
        health("idle", "2026-10-08T10:14:50+09:00", "2026-10-08T13:00:00+09:00"),
      ),
    ).toBe("10:14");
  });

  it("오늘이 아니면 MM/DD HH:MM", () => {
    expect(
      formatLastCollectedAt(
        health("idle", "2026-10-07T10:14:50+09:00", "2026-10-08T05:00:00+09:00"),
      ),
    ).toBe("10/07 10:14");
  });
});

describe("toCollectionStatusView", () => {
  it("ok: 1분 미만은 '마지막 갱신 방금'", () => {
    expect(toCollectionStatusView(input(health("ok", "2026-10-08T07:31:08+09:00")))).toEqual({
      tone: "ok",
      title: "수집 정상",
      detail: "마지막 갱신 방금",
    });
  });

  it("ok: 59초까지 방금, 60초부터 1분 전", () => {
    const value = health("ok", "2026-10-08T07:30:21+09:00");
    expect(toCollectionStatusView(input(value)).detail).toBe("마지막 갱신 방금");
    expect(toCollectionStatusView(input(value, { clientNowMs: RECEIVED_AT + 1_000 })).detail).toBe(
      "마지막 갱신 1분 전",
    );
  });

  it("ok 라도 기기 경과를 더한 마지막 갱신이 3분을 넘으면 주의 표시", () => {
    // 응답 때 12초 전 → 기기 경과 168초를 더하면 정확히 180초(아직 ok)
    const value = health("ok", "2026-10-08T07:31:08+09:00");
    expect(toCollectionStatusView(input(value, { clientNowMs: RECEIVED_AT + 168_000 })).tone).toBe(
      "ok",
    );
    const stale = toCollectionStatusView(input(value, { clientNowMs: RECEIVED_AT + 169_000 }));
    expect(stale).toEqual({ tone: "warning", title: "수집 정상", detail: "마지막 갱신 3분 전" });
    expect(OK_STALE_AFTER_SEC).toBe(180);
  });

  it("degraded: 주의 표시와 수집 지연 · 마지막 갱신 N분 전", () => {
    expect(toCollectionStatusView(input(health("degraded", "2026-10-08T07:26:00+09:00")))).toEqual({
      tone: "warning",
      title: "수집 지연",
      detail: "마지막 갱신 5분 전",
    });
  });

  it("idle: 수집 시간이 아니라는 안내와 마지막 수집 시각", () => {
    expect(
      toCollectionStatusView(
        input(health("idle", "2026-10-07T10:14:50+09:00", "2026-10-07T20:00:00+09:00")),
      ),
    ).toEqual({
      tone: "idle",
      title: COLLECTION_STATUS_TITLE.idle,
      detail: "마지막 수집 10:14",
    });
  });

  it("lastSuccessAt 이 없으면 수집 기록 없음", () => {
    expect(toCollectionStatusView(input(health("ok", null)))).toEqual({
      tone: "warning",
      title: COLLECTION_NO_RECORD,
      detail: null,
    });
    expect(toCollectionStatusView(input(health("idle", null))).detail).toBe(COLLECTION_NO_RECORD);
  });

  it("lastSuccessAt 을 해석할 수 없으면 경과 없이 상태 이름만", () => {
    expect(toCollectionStatusView(input(health("ok", "not-a-time")))).toEqual({
      tone: "ok",
      title: "수집 정상",
      detail: null,
    });
  });

  it("다시 부르기가 실패하는 중이면 상태 이름은 두고 '상태 확인 지연'을 붙여 주의 표시", () => {
    expect(
      toCollectionStatusView(
        input(health("ok", "2026-10-08T07:31:08+09:00"), { isRefreshFailing: true }),
      ),
    ).toEqual({
      tone: "warning",
      title: "수집 정상",
      detail: "마지막 갱신 방금 · 상태 확인 지연",
    });
    expect(toCollectionStatusView(input(health("ok", null), { isRefreshFailing: true }))).toEqual({
      tone: "warning",
      title: COLLECTION_NO_RECORD,
      detail: "상태 확인 지연",
    });
  });

  it("계약에 없는 status 는 '확인할 수 없어요'로 본다", () => {
    const unknownStatus = {
      ...health("ok", "2026-10-08T07:31:08+09:00"),
      status: "paused",
    } as unknown as HealthResponse;
    expect(toCollectionStatusView(input(unknownStatus))).toEqual({
      tone: "unknown",
      title: COLLECTION_STATUS_UNAVAILABLE,
      detail: null,
    });
  });
});

describe("healthRefetchDelayMs", () => {
  it("평소와 일반 실패는 30초", () => {
    expect(healthRefetchDelayMs(null)).toBe(HEALTH_REFETCH_INTERVAL_MS);
    expect(
      healthRefetchDelayMs(new ApiError({ code: "NETWORK_ERROR", status: 0, message: "x" })),
    ).toBe(30_000);
  });

  it("429 는 Retry-After 가 더 길면 그것을 따른다", () => {
    const rateLimited = (retryAfterSec: number) =>
      new ApiError({ code: "RATE_LIMITED", status: 429, message: "x", retryAfterSec });
    expect(healthRefetchDelayMs(rateLimited(90))).toBe(90_000);
    expect(healthRefetchDelayMs(rateLimited(5))).toBe(30_000);
  });
});
