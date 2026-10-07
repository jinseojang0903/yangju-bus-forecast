import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../../api/client";
import {
  ERROR_REFRESH_DELAY_MS,
  errorRefreshDelayMs,
  MAX_REFRESH_DELAY_MS,
  MIN_REFRESH_DELAY_MS,
  snapshotRefreshDelayMs,
} from "./refresh";

const computedAt = "2026-10-07T07:31:20+09:00";

describe("snapshotRefreshDelayMs", () => {
  it("서버 간격(nextRefreshAt - computedAt)만큼 기다린다", () => {
    expect(snapshotRefreshDelayMs({ nextRefreshAt: "2026-10-07T07:31:33+09:00", computedAt })).toBe(
      13_000,
    );
  });

  it("기기 시계가 앞서거나 늦어도 같은 간격이다", () => {
    const timing = { nextRefreshAt: "2026-10-07T07:31:33+09:00", computedAt };
    vi.useFakeTimers();
    try {
      vi.setSystemTime(new Date("2026-10-07T08:31:20+09:00"));
      expect(snapshotRefreshDelayMs(timing)).toBe(13_000);
      vi.setSystemTime(new Date("2026-10-07T06:31:20+09:00"));
      expect(snapshotRefreshDelayMs(timing)).toBe(13_000);
    } finally {
      vi.useRealTimers();
    }
  });

  it("간격이 10초보다 짧거나 거꾸로면 최소 간격(10초)을 지킨다", () => {
    expect(MIN_REFRESH_DELAY_MS).toBe(10_000);
    expect(snapshotRefreshDelayMs({ nextRefreshAt: "2026-10-07T07:31:23+09:00", computedAt })).toBe(
      MIN_REFRESH_DELAY_MS,
    );
    expect(snapshotRefreshDelayMs({ nextRefreshAt: "2026-10-07T07:31:00+09:00", computedAt })).toBe(
      MIN_REFRESH_DELAY_MS,
    );
  });

  it("서비스 시간 밖처럼 먼 시각은 상한(30분)까지만 기다린다", () => {
    expect(snapshotRefreshDelayMs({ nextRefreshAt: "2026-10-08T05:45:00+09:00", computedAt })).toBe(
      MAX_REFRESH_DELAY_MS,
    );
  });

  it("시각을 해석할 수 없으면 오류 간격으로 다시 시도한다", () => {
    expect(snapshotRefreshDelayMs({ nextRefreshAt: "bad", computedAt })).toBe(
      ERROR_REFRESH_DELAY_MS,
    );
  });
});

describe("errorRefreshDelayMs", () => {
  it("400·404 는 자동으로 다시 부르지 않는다", () => {
    expect(errorRefreshDelayMs(new ApiError({ code: "NOT_FOUND", status: 404, message: "" }))).toBe(
      false,
    );
    expect(
      errorRefreshDelayMs(new ApiError({ code: "VALIDATION_FAILED", status: 400, message: "" })),
    ).toBe(false);
  });

  it("429 는 Retry-After 를 따른다", () => {
    const error = new ApiError({
      code: "RATE_LIMITED",
      status: 429,
      message: "",
      retryAfterSec: 20,
    });
    expect(errorRefreshDelayMs(error)).toBe(20_000);
  });

  it("네트워크 오류는 정해진 간격으로 다시 시도한다", () => {
    expect(
      errorRefreshDelayMs(new ApiError({ code: "NETWORK_ERROR", status: 0, message: "" })),
    ).toBe(ERROR_REFRESH_DELAY_MS);
  });
});
