import { describe, expect, it } from "vitest";
import {
  formatKstDateTime,
  formatKstHourMinute,
  formatKstMonthDayHourMinute,
  formatKstTime,
  isSameInstant,
  isSameKstDate,
  toElapsed,
  UNKNOWN_TIME,
} from "./time";

describe("toElapsed", () => {
  it("60초 미만은 '방금'으로 묶는다(경계 59·60초)", () => {
    expect(toElapsed(0)).toEqual({ unit: "justNow" });
    expect(toElapsed(59)).toEqual({ unit: "justNow" });
    expect(toElapsed(60)).toEqual({ unit: "minute", value: 1 });
  });

  it("60분 미만은 분, 그 이상은 시간(내림)", () => {
    expect(toElapsed(119)).toEqual({ unit: "minute", value: 1 });
    expect(toElapsed(3599)).toEqual({ unit: "minute", value: 59 });
    expect(toElapsed(3600)).toEqual({ unit: "hour", value: 1 });
    expect(toElapsed(26 * 3600 + 59 * 60)).toEqual({ unit: "hour", value: 26 });
  });

  it("음수·NaN 은 '방금'", () => {
    expect(toElapsed(-5)).toEqual({ unit: "justNow" });
    expect(toElapsed(Number.NaN)).toEqual({ unit: "justNow" });
  });
});

describe("KST 시각 표시", () => {
  it("계산 시점을 초까지 보인다", () => {
    expect(formatKstTime("2026-10-07T07:31:20+09:00")).toBe("07:31:20");
  });

  it("다른 오프셋으로 와도 KST 로 바꾼다", () => {
    expect(formatKstHourMinute("2026-10-06T22:38:40Z")).toBe("07:38");
  });

  it("다음 예보 시작을 날짜·요일과 함께 보인다", () => {
    expect(formatKstDateTime("2026-10-08T05:45:00+09:00")).toBe("10월 8일(목) 05:45");
  });

  it("자정은 00 으로 보인다", () => {
    expect(formatKstHourMinute("2026-10-08T00:05:00+09:00")).toBe("00:05");
  });

  it("해석할 수 없거나 없으면 자리표시를 보인다", () => {
    expect(formatKstHourMinute(null)).toBe(UNKNOWN_TIME);
    expect(formatKstHourMinute("not-a-time")).toBe(UNKNOWN_TIME);
  });

  it("월/일은 두 자리로 채워 MM/DD HH:MM 으로 보인다", () => {
    expect(formatKstMonthDayHourMinute("2026-10-07T10:14:50+09:00")).toBe("10/07 10:14");
    expect(formatKstMonthDayHourMinute("2026-01-05T06:00:00+09:00")).toBe("01/05 06:00");
    expect(formatKstMonthDayHourMinute(null)).toBe(UNKNOWN_TIME);
  });

  it("같은 날짜인지는 KST 기준으로 본다", () => {
    // UTC 로는 10/7 이지만 KST 로는 10/8 00:30
    expect(isSameKstDate("2026-10-07T15:30:00Z", "2026-10-08T07:00:00+09:00")).toBe(true);
    expect(isSameKstDate("2026-10-07T23:59:59+09:00", "2026-10-08T00:00:00+09:00")).toBe(false);
    expect(isSameKstDate("2025-10-08T07:00:00+09:00", "2026-10-08T07:00:00+09:00")).toBe(false);
    expect(isSameKstDate("not-a-time", "2026-10-08T07:00:00+09:00")).toBe(false);
  });

  it("같은 순간이면 표기가 달라도 같다고 본다", () => {
    expect(isSameInstant("2026-10-07T07:31:20+09:00", "2026-10-06T22:31:20Z")).toBe(true);
    expect(isSameInstant("2026-10-07T07:31:20+09:00", "2026-10-07T07:31:21+09:00")).toBe(false);
  });
});
