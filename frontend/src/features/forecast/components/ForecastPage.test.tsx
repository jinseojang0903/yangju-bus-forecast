import { screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { SnapshotResponse } from "../../../api/types";
import { FORECAST_STATUS_TEXT, SWITCH_SUGGESTION_TITLE } from "../../../lib/labels";
import {
  exampleExplanation,
  examplePositions,
  exampleSnapshot,
  outsideCollectionSnapshot,
  snapshotWithSelectedStatus,
} from "../../../test/fixtures";
import { errorBody, mockFetch, requestedUrls } from "../../../test/mockFetch";
import { renderApp } from "../../../test/render";

// 지도 그리기(Leaflet)는 components/map/RouteMap.test.tsx 에서 따로 본다.
vi.mock("../../../components/map/RouteMap", () => ({
  RouteMap: ({ label }: { label: string }) => <section aria-label={label} />,
}));

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

  it("노선 지도는 실제 positions API 를 부르고(scenario 없이) 버스 목록 아래에 그린다", async () => {
    const snapshot = snapshotWithSelectedStatus("insufficient_cases");
    const fetchMock = mockFetch([
      { path: "/api/v1/snapshot", body: snapshot },
      { path: "/api/v1/routes/235000092/positions", body: examplePositions },
      {
        path: "/api/v1/routes/235000123/positions",
        status: 404,
        body: errorBody("NOT_FOUND", "리소스를 찾을 수 없습니다"),
      },
    ]);
    const { user } = renderApp(`${FORECAST_URL}&scenario=status_insufficient_cases`);
    const positionUrls = () =>
      requestedUrls(fetchMock).filter((url) => url.pathname.startsWith("/api/v1/routes/"));

    expect(await screen.findByText("07:31:10 수집 기준")).toBeInTheDocument();

    // 버스 목록 바로 다음에 노선 지도가 온다
    const busHeading = screen.getByRole("heading", { name: "도착 예정 버스" });
    const mapHeading = screen.getByRole("heading", { name: "노선 지도" });
    expect(busHeading.compareDocumentPosition(mapHeading)).toBe(Node.DOCUMENT_POSITION_FOLLOWING);
    const busSection = busHeading.closest("section");
    expect(busSection?.nextElementSibling).toBe(mapHeading.closest("section"));

    // 처음에는 고른 노선(G1300)만 부른다
    expect(positionUrls().map((url) => url.pathname)).toEqual([
      "/api/v1/routes/235000092/positions",
    ]);

    await user.click(screen.getByRole("button", { name: "1306" }));
    expect(await screen.findByText("이 노선은 아직 지도를 제공하지 않아요.")).toBeInTheDocument();
    expect(positionUrls().map((url) => url.pathname)).toEqual([
      "/api/v1/routes/235000092/positions",
      "/api/v1/routes/235000123/positions",
    ]);
    for (const url of positionUrls()) expect(url.search).toBe("");
  });

  it("노선 지도가 404 여도 예보 화면은 그대로 보인다", async () => {
    mockSnapshotApi();
    const { user } = renderApp(FORECAST_URL);

    const g1300 = await screen.findByRole("article", { name: "G1300 버스" });
    expect(within(g1300).getByText("24회 중 18회")).toBeInTheDocument();
    expect(await screen.findByText("이 노선은 아직 지도를 제공하지 않아요.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "1306" }));
    expect(screen.getByRole("article", { name: "G1300 버스" })).toBeInTheDocument();
    expect(screen.queryByText("정류장이나 목적지를 찾을 수 없어요.")).toBeNull();
  });

  it("조회 조건이 없으면 조건을 고르라고 안내한다", async () => {
    mockFetch([]);
    renderApp("/forecast");
    expect(await screen.findByText("조회 조건이 없어요")).toBeInTheDocument();
  });
});
