import { describe, expect, it } from "vitest";
import { readSnapshotRequest, toForecastSearch } from "./snapshotRequest";

const withScenario = new URLSearchParams(
  "station=235000392&destination=jamsil&deadline=08:30&scenario=status_insufficient_cases",
);

describe("readSnapshotRequest", () => {
  it("개발 빌드에서는 scenario 를 그대로 넘긴다", () => {
    expect(readSnapshotRequest(withScenario, true)).toEqual({
      station: "235000392",
      destination: "jamsil",
      deadline: "08:30",
      scenario: "status_insufficient_cases",
    });
  });

  it("운영 빌드에서는 scenario 를 버린다", () => {
    expect(readSnapshotRequest(withScenario, false)?.scenario).toBeNull();
  });

  it("정류장이나 목적지가 없으면 null", () => {
    expect(readSnapshotRequest(new URLSearchParams("station=235000392"), true)).toBeNull();
  });

  it("마감이 없으면 undefined 로 둔다", () => {
    const request = readSnapshotRequest(
      new URLSearchParams("station=235000392&destination=jamsil"),
      true,
    );
    expect(request?.deadline).toBeUndefined();
  });
});

describe("toForecastSearch", () => {
  it("마감이 있으면 넣고 없으면 뺀다", () => {
    const withDeadline = new URLSearchParams(
      toForecastSearch({ station: "235000392", destination: "jamsil", deadline: "08:30" }),
    );
    expect(withDeadline.get("deadline")).toBe("08:30");

    const withoutDeadline = new URLSearchParams(
      toForecastSearch({ station: "235000392", destination: "jamsil" }),
    );
    expect(withoutDeadline.has("deadline")).toBe(false);
  });
});
