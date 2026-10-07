import { describe, expect, it } from "vitest";
import { exampleSnapshot } from "../../test/fixtures";
import { findCandidateForecast, isRecommendedCandidate } from "./candidates";

const [g1300, bus1306] = exampleSnapshot.alternatives.candidates;

describe("isRecommendedCandidate", () => {
  it("노선과 차량이 모두 같아야 추천이다", () => {
    const { recommended } = exampleSnapshot.alternatives;
    expect(isRecommendedCandidate(bus1306, recommended)).toBe(true);
    expect(isRecommendedCandidate(g1300, recommended)).toBe(false);
    expect(isRecommendedCandidate(bus1306, null)).toBe(false);
  });
});

describe("findCandidateForecast", () => {
  it("같은 버스의 같은 선행시간 예보를 찾는다", () => {
    const forecast = findCandidateForecast(g1300, exampleSnapshot.buses);
    expect(forecast?.leadTimeMin).toBe(10);
    expect(forecast?.n).toBe(24);
    expect(forecast?.k).toBe(18);
  });

  it("vehicleId 가 없는 후보(timetable_next)는 buses 와 매칭하지 않는다", () => {
    const timetableNext = { ...g1300, source: "timetable_next" as const, vehicleId: null };
    const buses = exampleSnapshot.buses.map((bus, index) =>
      index === 0 ? { ...bus, source: "timetable_next" as const, vehicleId: null } : bus,
    );
    expect(findCandidateForecast(timetableNext, buses)).toBeNull();
  });

  it("선행시간이 없으면 null", () => {
    expect(
      findCandidateForecast({ ...g1300, leadTimeMin: null }, exampleSnapshot.buses),
    ).toBeNull();
  });
});
