import { describe, expect, it } from "vitest";
import { formatMinutesToArrival, formatPercent, formatSeats } from "./format";

describe("formatPercent", () => {
  it("등급 경계(70%·30%)와 표시가 어긋나지 않도록 내림한다", () => {
    expect(formatPercent(0.695)).toBe("69%");
    expect(formatPercent(0.7)).toBe("70%");
    expect(formatPercent(0.299)).toBe("29%");
    expect(formatPercent(0.3)).toBe("30%");
  });

  it("부동소수 오차로 한 단계 내려가지 않는다", () => {
    expect(formatPercent(0.29)).toBe("29%");
    expect(formatPercent(0.57)).toBe("57%");
    expect(formatPercent(0.75)).toBe("75%");
  });

  it("0과 1", () => {
    expect(formatPercent(0)).toBe("0%");
    expect(formatPercent(1)).toBe("100%");
  });
});

describe("formatSeats / formatMinutesToArrival", () => {
  it("값이 없으면 안내 문구", () => {
    expect(formatSeats(3)).toBe("3석");
    expect(formatSeats(null)).toBe("정보 없음");
    expect(formatMinutesToArrival(0)).toBe("곧 도착");
    expect(formatMinutesToArrival(7)).toBe("7분 후");
  });
});
