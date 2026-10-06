import { describe, expect, it } from "vitest";
import { exampleSnapshot, snapshotWithSelectedStatus } from "../../test/fixtures";
import { findSelectedForecast, toBusForecastView } from "./busForecast";

describe("findSelectedForecast", () => {
  it("서버가 고른 선행시간의 예보를 쓴다", () => {
    expect(findSelectedForecast(exampleSnapshot.buses[0])?.leadTimeMin).toBe(10);
    expect(findSelectedForecast(exampleSnapshot.buses[1])?.leadTimeMin).toBe(15);
  });
});

describe("toBusForecastView", () => {
  it("ok 면 서버의 확률·n·k·등급을 그대로 옮긴다", () => {
    expect(toBusForecastView(exampleSnapshot.buses[0], false)).toEqual({
      kind: "probability",
      leadTimeMin: 10,
      issuedAt: "2026-10-07T07:28:40+09:00",
      noSeatProbability: 0.75,
      n: 24,
      k: 18,
      riskLevel: "high",
      preliminary: true,
    });
  });

  it("스냅샷이 오래됐으면 확률 대신 정보 오래됨", () => {
    expect(toBusForecastView(exampleSnapshot.buses[0], true)).toEqual({
      kind: "status",
      status: "stale",
    });
  });

  it("고른 선행시간이 없으면 아직 시점 전", () => {
    const bus = snapshotWithSelectedStatus("not_yet").buses[0];
    expect(toBusForecastView(bus, false)).toEqual({ kind: "status", status: "not_yet" });
  });

  it("사례 부족이면 그 상태를 보인다", () => {
    const bus = snapshotWithSelectedStatus("insufficient_cases").buses[0];
    expect(toBusForecastView(bus, false)).toEqual({
      kind: "status",
      status: "insufficient_cases",
    });
  });
});
