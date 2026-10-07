import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { FORECAST_STATUS_TEXT } from "../../../lib/labels";
import { cloneSnapshot, exampleSnapshot, snapshotWithSelectedStatus } from "../../../test/fixtures";
import { BusForecastCard } from "./BusForecastCard";

const NON_OK_STATUSES = [
  "not_yet",
  "insufficient_cases",
  "not_validated",
  "stale",
  "missing_input",
  "outside_hours",
] as const;

describe("BusForecastCard", () => {
  it("ok: 위험 문구, 'n회 중 k회', 예비, 현재 잔여석을 함께 보인다", () => {
    render(
      <BusForecastCard
        bus={exampleSnapshot.buses[0]}
        isSnapshotStale={false}
        dataUpdatedAt={exampleSnapshot.dataUpdatedAt}
      />,
    );
    expect(screen.getByText("무좌석 위험")).toBeInTheDocument();
    expect(screen.getByText("위험 높음")).toBeInTheDocument();
    expect(screen.getByText("24회 중 18회")).toBeInTheDocument();
    expect(screen.getByText("예비")).toBeInTheDocument();
    expect(screen.getByText("3석")).toBeInTheDocument();
    expect(screen.getByText("7분 후")).toBeInTheDocument();
  });

  it.each(NON_OK_STATUSES)("%s: 상태 문구와 현재 잔여석만 보이고 확률은 없다", (status) => {
    const snapshot = snapshotWithSelectedStatus(status);
    render(
      <BusForecastCard
        bus={snapshot.buses[0]}
        isSnapshotStale={false}
        dataUpdatedAt={snapshot.dataUpdatedAt}
      />,
    );
    expect(screen.getByText(FORECAST_STATUS_TEXT[status].title)).toBeInTheDocument();
    expect(screen.getByText(FORECAST_STATUS_TEXT[status].description)).toBeInTheDocument();
    expect(screen.getByText("3석")).toBeInTheDocument();
    expect(screen.queryByText(/회 중/)).toBeNull();
    expect(screen.queryByText("위험 높음")).toBeNull();
  });

  it("스냅샷 전체가 오래됐으면 ok 예보라도 확률 대신 정보 오래됨", () => {
    render(
      <BusForecastCard
        bus={exampleSnapshot.buses[0]}
        isSnapshotStale
        dataUpdatedAt={exampleSnapshot.dataUpdatedAt}
      />,
    );
    expect(screen.getByText(FORECAST_STATUS_TEXT.stale.title)).toBeInTheDocument();
    expect(screen.getByText("07:31:10")).toBeInTheDocument();
    expect(screen.queryByText("24회 중 18회")).toBeNull();
  });

  it("분 단위 도착 예상이면 작은 표시를 붙인다", () => {
    const bus = cloneSnapshot().buses[0];
    bus.arrivalEstimateSource = "predict_time_min";
    render(<BusForecastCard bus={bus} isSnapshotStale={false} dataUpdatedAt={null} />);
    expect(screen.getByText("도착 예상(분 단위)")).toBeInTheDocument();
  });

  it("예비가 아니면 예비 표기를 붙이지 않는다", () => {
    const bus = cloneSnapshot().buses[0];
    for (const forecast of bus.forecasts) forecast.preliminary = false;
    render(<BusForecastCard bus={bus} isSnapshotStale={false} dataUpdatedAt={null} />);
    expect(screen.getByText("위험 높음")).toBeInTheDocument();
    expect(screen.queryByText("예비")).toBeNull();
  });
});
