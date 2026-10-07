import { describe, expect, it } from "vitest";
import {
  formatKstDateTime,
  formatKstHourMinute,
  formatKstTime,
  isSameInstant,
  UNKNOWN_TIME,
} from "./time";

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

  it("같은 순간이면 표기가 달라도 같다고 본다", () => {
    expect(isSameInstant("2026-10-07T07:31:20+09:00", "2026-10-06T22:31:20Z")).toBe(true);
    expect(isSameInstant("2026-10-07T07:31:20+09:00", "2026-10-07T07:31:21+09:00")).toBe(false);
  });
});
