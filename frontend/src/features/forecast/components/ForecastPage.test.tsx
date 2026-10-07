import { screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { SnapshotResponse } from "../../../api/types";
import { FORECAST_STATUS_TEXT, SWITCH_SUGGESTION_TITLE } from "../../../lib/labels";
import {
  exampleExplanation,
  exampleSnapshot,
  outsideCollectionSnapshot,
  snapshotWithSelectedStatus,
} from "../../../test/fixtures";
import { errorBody, mockFetch, requestedUrls } from "../../../test/mockFetch";
import { renderApp } from "../../../test/render";

const FORECAST_URL = "/forecast?station=235000392&destination=jamsil&deadline=08:30";

function mockSnapshotApi(snapshot: SnapshotResponse = exampleSnapshot) {
  return mockFetch([
    { path: "/api/v1/snapshot", body: snapshot },
    {
      path: `/api/v1/snapshot/${snapshot.snapshotId}/explanation`,
      body: {
        ...exampleExplanation,
        snapshotId: snapshot.snapshotId,
        computedAt: snapshot.computedAt,
      },
    },
  ]);
}

describe("ForecastPage", () => {
  it("9장 예시: 계산 시점, 예비, G1300 위험 높음·24회 중 18회, 바꿔 타기 강조", async () => {
    mockSnapshotApi();
    renderApp(FORECAST_URL);

    expect(await screen.findByText("07:31:20")).toBeInTheDocument();
    expect(screen.getAllByText("예비").length).toBeGreaterThan(0);

    const g1300 = screen.getByRole("article", { name: "G1300 버스" });
    expect(within(g1300).getByText("위험 높음")).toBeInTheDocument();
    expect(within(g1300).getByText("24회 중 18회")).toBeInTheDocument();
    expect(within(g1300).getByText("예비")).toBeInTheDocument();

    const bus1306 = screen.getByRole("article", { name: "1306 버스" });
    expect(within(bus1306).getByText("위험 낮음")).toBeInTheDocument();
    expect(within(bus1306).getByText("25회 중 5회")).toBeInTheDocument();

    expect(screen.getByText(SWITCH_SUGGESTION_TITLE)).toBeInTheDocument();
  });

  it("설명은 숫자를 그린 뒤 붙는다", async () => {
    mockSnapshotApi();
    renderApp(FORECAST_URL);
    expect(await screen.findByText(exampleExplanation.text)).toBeInTheDocument();
    expect(screen.getByText("기본 안내")).toBeInTheDocument();
  });

  it("개발 빌드에서는 scenario 를 /snapshot 에 그대로 넘기고 상태 안내를 보인다", async () => {
    const fetchMock = mockSnapshotApi(snapshotWithSelectedStatus("insufficient_cases"));
    renderApp(`${FORECAST_URL}&scenario=status_insufficient_cases`);

    expect(
      await screen.findByText(FORECAST_STATUS_TEXT.insufficient_cases.title),
    ).toBeInTheDocument();
    const g1300 = screen.getByRole("article", { name: "G1300 버스" });
    expect(within(g1300).getByText("3석")).toBeInTheDocument();
    expect(within(g1300).queryByText(/회 중/)).toBeNull();

    const snapshotUrl = requestedUrls(fetchMock).find((url) => url.pathname === "/api/v1/snapshot");
    expect(snapshotUrl?.searchParams.get("scenario")).toBe("status_insufficient_cases");
    expect(snapshotUrl?.searchParams.get("station")).toBe("235000392");
    expect(snapshotUrl?.searchParams.get("destination")).toBe("jamsil");
    expect(snapshotUrl?.searchParams.get("deadline")).toBe("08:30");
  });

  it("서비스 시간 밖이면 오류가 아니라 다음 예보 시작을 보인다", async () => {
    mockSnapshotApi(outsideCollectionSnapshot());
    renderApp(`${FORECAST_URL}&scenario=service_outside_collection`);

    expect(await screen.findByText("지금은 예보 시간이 아니에요")).toBeInTheDocument();
    expect(screen.getByText("10월 8일(목) 05:45")).toBeInTheDocument();
    expect(screen.getByText("07:31:20")).toBeInTheDocument();
    expect(screen.queryByText("대안 비교")).toBeNull();
    expect(screen.queryByRole("button", { name: "다시 시도" })).toBeNull();
  });

  it("404 면 조건을 다시 고르게 안내한다", async () => {
    mockFetch([
      {
        path: "/api/v1/snapshot",
        status: 404,
        body: errorBody("NOT_FOUND", "리소스를 찾을 수 없습니다"),
      },
    ]);
    renderApp(FORECAST_URL);
    expect(await screen.findByText("정류장이나 목적지를 찾을 수 없어요.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "조건 다시 고르기" })).toBeInTheDocument();
  });

  it("조회 조건이 없으면 조건을 고르라고 안내한다", async () => {
    mockFetch([]);
    renderApp("/forecast");
    expect(await screen.findByText("조회 조건이 없어요")).toBeInTheDocument();
  });
});
