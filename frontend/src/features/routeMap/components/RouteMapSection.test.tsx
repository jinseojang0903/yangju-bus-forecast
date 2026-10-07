import { act, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "../../../api/queryKeys";
import type { RoutePositionsResponse } from "../../../api/types";
import type { RouteMapProps } from "../../../components/map/types";
import {
  clonePositions,
  examplePositions,
  exampleSnapshot,
  exampleStationRoutes,
  outsideCollectionSnapshot,
} from "../../../test/fixtures";
import { errorBody, mockFetch, requestedUrls } from "../../../test/mockFetch";
import { renderWithProviders } from "../../../test/render";
import { RouteMapSection } from "./RouteMapSection";

// Leaflet 은 jsdom 에서 크기·타일을 계산하지 못한다. 지도는 받은 입력만 보이는 가짜로 바꾼다.
vi.mock("../../../components/map/RouteMap", () => ({
  RouteMap: (props: RouteMapProps) => (
    <section aria-label={props.label} data-focus-request={props.focusRequestId}>
      <ul>
        {props.vehicles.map((vehicle) => (
          <li key={vehicle.key} data-tone={vehicle.tone}>
            {vehicle.label}
          </li>
        ))}
      </ul>
    </section>
  ),
}));

const G1300_ID = "235000092";
const G1300_PATH = `/api/v1/routes/${G1300_ID}/positions`;
const R1306_PATH = "/api/v1/routes/235000123/positions";
// 버튼 글자는 "G1300 지도 없음"이다. jsdom 의 접근성 이름 계산은 span 앞 공백을 버려 공백 유무를 따지지 않는다.
const G1300_UNAVAILABLE = /^G1300\s*지도 없음$/;
const APPROACHING_LIST = { name: "내 정류장으로 오는 차량" };
const NOT_FOUND = { status: 404, body: errorBody("NOT_FOUND", "리소스를 찾을 수 없습니다") };

function positions1306(): RoutePositionsResponse {
  const positions = clonePositions();
  positions.routeId = "235000123";
  positions.routeName = "1306";
  positions.vehicles = [
    {
      vehicleId: "235010110",
      plateNo: null,
      stationSeq: 2,
      stationId: "235000002",
      state: "arrived",
      remainSeats: 14,
      stopsToTarget: 3,
    },
  ];
  return positions;
}

function mockPositions(g1300: RoutePositionsResponse = examplePositions) {
  return mockFetch([
    { path: G1300_PATH, body: g1300 },
    { path: R1306_PATH, body: positions1306() },
  ]);
}

function callsTo(fetchMock: ReturnType<typeof mockFetch>, path: string) {
  return requestedUrls(fetchMock).filter((url) => url.pathname === path).length;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("RouteMapSection", () => {
  it("수집 시각, 지도, 내 정류장으로 오는 차량을 가까운 순으로 보인다", async () => {
    const fetchMock = mockPositions();
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);

    const map = await screen.findByRole("region", { name: "G1300 노선 지도" });
    expect(screen.getByText("07:31:10 수집 기준")).toBeInTheDocument();
    expect(within(map).getByText("0석")).toHaveAttribute("data-tone", "noSeat");
    expect(within(map).getAllByText("정보 없음")[0]).toHaveAttribute("data-tone", "unknown");

    const list = screen.getByRole("list", APPROACHING_LIST);
    expect(
      within(list)
        .getAllByRole("listitem")
        .map((item) => item.textContent),
    ).toEqual([
      "덕현초교까지 1정류장 · 잔여석 정보 없음",
      "덕현초교까지 2정류장 · 잔여 5석",
      "덕현초교까지 4정류장 · 잔여 0석",
    ]);

    // 고른 노선만 부른다. 개발용 scenario 같은 쿼리는 붙이지 않는다.
    const paths = requestedUrls(fetchMock).map((url) => url.pathname + url.search);
    expect(paths).toEqual([G1300_PATH]);
  });

  it("노선 버튼을 누르면 그 노선을 그때 받아 지도를 바꾼다", async () => {
    const fetchMock = mockPositions();
    const { user } = renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);
    await screen.findByRole("region", { name: "G1300 노선 지도" });
    expect(callsTo(fetchMock, R1306_PATH)).toBe(0);

    const button1306 = screen.getByRole("button", { name: "1306" });
    expect(button1306).toHaveAttribute("aria-pressed", "false");
    await user.click(button1306);

    expect(button1306).toHaveAttribute("aria-pressed", "true");
    expect(await screen.findByRole("region", { name: "1306 노선 지도" })).toBeInTheDocument();
    expect(callsTo(fetchMock, R1306_PATH)).toBe(1);
    const list = screen.getByRole("list", APPROACHING_LIST);
    expect(within(list).getByRole("listitem")).toHaveTextContent(
      "덕현초교까지 3정류장 · 잔여 14석",
    );
  });

  it("'내 정류장 중심으로'를 누르면 지도에 다시 맞추기를 요청한다", async () => {
    mockPositions();
    const { user } = renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);
    const map = await screen.findByRole("region", { name: "G1300 노선 지도" });
    expect(map).toHaveAttribute("data-focus-request", "0");

    await user.click(screen.getByRole("button", { name: "내 정류장 중심으로" }));
    expect(screen.getByRole("region", { name: "G1300 노선 지도" })).toHaveAttribute(
      "data-focus-request",
      "1",
    );
  });

  it("stale 이면 '정보 오래됨'을 알리고 지도는 그대로 둔다", async () => {
    const stale = clonePositions();
    stale.stale = true;
    mockPositions(stale);
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);

    expect(await screen.findByText("정보 오래됨")).toBeInTheDocument();
    expect(screen.getByRole("region", { name: "G1300 노선 지도" })).toBeInTheDocument();
  });

  it("수집 시간 밖이면 마지막 수집 기준이라고 알린다", async () => {
    const outside = clonePositions();
    outside.inCollectionWindow = false;
    mockPositions(outside);
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);

    expect(await screen.findByText("지금은 수집 시간이 아니에요")).toBeInTheDocument();
    expect(screen.getByText(/마지막 수집 기준/)).toBeInTheDocument();
    expect(screen.queryByText("정보 오래됨")).toBeNull();
  });

  it("수집 기록이 없으면 '수집 기록 없음'", async () => {
    const empty = clonePositions();
    empty.dataUpdatedAt = null;
    empty.vehicles = [];
    mockPositions(empty);
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);

    expect(await screen.findByText(/수집 기록 없음/)).toBeInTheDocument();
    expect(screen.getByText("지금 내 정류장으로 오는 차량이 없어요.")).toBeInTheDocument();
  });

  it("404 면 지도 영역에만 알리고, 그 노선 버튼은 '지도 없음'으로 비활성이 된다", async () => {
    mockFetch([
      { path: G1300_PATH, ...NOT_FOUND },
      { path: R1306_PATH, body: positions1306() },
    ]);
    const { user } = renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);

    expect(await screen.findByText("이 노선은 아직 지도를 제공하지 않아요.")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "조건 다시 고르기" })).toBeNull();
    expect(screen.queryByText("리소스를 찾을 수 없습니다")).toBeNull();

    const buttonG1300 = screen.getByRole("button", { name: G1300_UNAVAILABLE });
    expect(buttonG1300).toHaveAttribute("aria-disabled", "true");
    const button1306 = screen.getByRole("button", { name: "1306" });
    expect(button1306).not.toHaveAttribute("aria-disabled");

    await user.click(button1306);
    expect(await screen.findByRole("region", { name: "1306 노선 지도" })).toBeInTheDocument();

    // 비활성 노선은 눌러도 바뀌지 않는다
    await user.click(buttonG1300);
    expect(button1306).toHaveAttribute("aria-pressed", "true");
    expect(screen.getByRole("region", { name: "1306 노선 지도" })).toBeInTheDocument();
  });

  it("400 도 지도 영역 안에서만 알리고 버튼을 비활성으로 한다", async () => {
    mockFetch([
      {
        path: G1300_PATH,
        status: 400,
        body: errorBody("VALIDATION_FAILED", "요청 형식이 올바르지 않습니다"),
      },
    ]);
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);
    expect(
      await screen.findByText("노선 정보를 확인할 수 없어 지도를 그리지 못했어요."),
    ).toBeInTheDocument();
    expect(screen.getByRole("button", { name: G1300_UNAVAILABLE })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
  });

  it.each([
    [
      "500",
      { status: 500, body: errorBody("INTERNAL_ERROR", "서버 오류") },
      "일시적인 문제로 정보를 불러오지 못했어요.",
    ],
    ["네트워크 오류", { networkError: true }, "인터넷 연결을 확인해 주세요."],
  ] as const)(
    "다시 조회가 %s 여도 이전 지도·목록은 남기고 짧게만 알린다",
    async (_name, failure, message) => {
      mockPositions();
      const { queryClient } = renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);
      await screen.findByRole("region", { name: "G1300 노선 지도" });

      mockFetch([{ path: G1300_PATH, ...failure }]);
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: queryKeys.routePositions(G1300_ID) });
      });

      expect(await screen.findByText(message)).toBeInTheDocument();
      expect(screen.getByRole("region", { name: "G1300 노선 지도" })).toBeInTheDocument();
      expect(screen.getByText("07:31:10 수집 기준")).toBeInTheDocument();
      expect(
        within(screen.getByRole("list", APPROACHING_LIST)).getAllByRole("listitem"),
      ).toHaveLength(3);
      // 잠깐의 실패는 노선을 비활성으로 만들지 않는다
      expect(screen.getByRole("button", { name: "G1300" })).not.toHaveAttribute("aria-disabled");
    },
  );

  it("고른 노선만 nextRefreshAt 간격(13초)으로 자동 재조회하고, 다른 노선은 부르지 않는다", async () => {
    // waitFor 가 돌 수 있게 실제 시간도 흐르게 둔다
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = mockPositions();
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);
    await screen.findByRole("region", { name: "G1300 노선 지도" });
    expect(callsTo(fetchMock, G1300_PATH)).toBe(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(13_000);
    });
    expect(callsTo(fetchMock, G1300_PATH)).toBe(2);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(13_000);
    });
    expect(callsTo(fetchMock, G1300_PATH)).toBe(3);
    expect(callsTo(fetchMock, R1306_PATH)).toBe(0);
  });

  it("404 노선은 자동으로 다시 부르지 않는다", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = mockFetch([{ path: G1300_PATH, ...NOT_FOUND }]);
    renderWithProviders(<RouteMapSection snapshot={exampleSnapshot} />);
    await screen.findByText("이 노선은 아직 지도를 제공하지 않아요.");

    await act(async () => {
      await vi.advanceTimersByTimeAsync(60_000);
    });
    expect(callsTo(fetchMock, G1300_PATH)).toBe(1);
  });

  it("스냅샷에 버스가 없으면(수집 시간 밖) 정류장 기준정보의 노선으로 지도를 그린다", async () => {
    const fetchMock = mockFetch([
      { path: "/api/v1/stations/235000392/routes", body: exampleStationRoutes },
      { path: G1300_PATH, body: examplePositions },
      { path: R1306_PATH, body: positions1306() },
    ]);
    renderWithProviders(<RouteMapSection snapshot={outsideCollectionSnapshot()} />);

    expect(await screen.findByRole("region", { name: "G1300 노선 지도" })).toBeInTheDocument();
    expect(requestedUrls(fetchMock).map((url) => url.pathname)).toContain(
      "/api/v1/stations/235000392/routes",
    );
  });
});
