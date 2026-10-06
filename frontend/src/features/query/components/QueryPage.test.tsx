import { fireEvent, screen, waitFor } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { PARSE_QUERY_TEXT } from "../../../lib/labels";
import {
  exampleExplanation,
  exampleSnapshot,
  exampleStationRoutes,
  exampleStations,
  unavailableParseQuery,
} from "../../../test/fixtures";
import { mockFetch, requestedUrls } from "../../../test/mockFetch";
import { renderApp } from "../../../test/render";

function mockQueryApi() {
  return mockFetch([
    { path: "/api/v1/stations", body: exampleStations },
    { path: "/api/v1/stations/235000392/routes", body: exampleStationRoutes },
    { method: "POST", path: "/api/v1/parse-query", body: unavailableParseQuery },
    { path: "/api/v1/snapshot", body: exampleSnapshot },
    {
      path: `/api/v1/snapshot/${exampleSnapshot.snapshotId}/explanation`,
      body: exampleExplanation,
    },
  ]);
}

describe("QueryPage", () => {
  it("정류장·목적지·마감을 골라 예보 화면으로 새로고침 없이 넘어간다", async () => {
    mockQueryApi();
    const { router, user } = renderApp("/");

    await screen.findByRole("option", { name: "잠실" });
    expect(screen.getByLabelText("출발 정류장")).toHaveValue("235000392");
    await waitFor(() => expect(screen.getByLabelText("목적지")).toHaveValue("jamsil"));
    expect(screen.getByText("이용 노선: G1300, 1306")).toBeInTheDocument();

    fireEvent.change(screen.getByLabelText("도착 마감 (선택)"), { target: { value: "08:30" } });
    await user.click(screen.getByRole("button", { name: "예보 보기" }));

    await waitFor(() => expect(router.state.location.pathname).toBe("/forecast"));
    const params = new URLSearchParams(router.state.location.search);
    expect(params.get("station")).toBe("235000392");
    expect(params.get("destination")).toBe("jamsil");
    expect(params.get("deadline")).toBe("08:30");

    expect(await screen.findByText("07:31:20")).toBeInTheDocument();
  });

  it("마감을 넣지 않으면 deadline 없이 넘어간다", async () => {
    mockQueryApi();
    const { router, user } = renderApp("/");
    await screen.findByRole("option", { name: "잠실" });
    await waitFor(() => expect(screen.getByLabelText("목적지")).toHaveValue("jamsil"));

    await user.click(screen.getByRole("button", { name: "예보 보기" }));
    await waitFor(() => expect(router.state.location.pathname).toBe("/forecast"));
    expect(new URLSearchParams(router.state.location.search).has("deadline")).toBe(false);
  });

  it("말로 묻기는 준비 중 안내(unavailable)를 보인다", async () => {
    const fetchMock = mockQueryApi();
    const { user } = renderApp("/");

    await user.type(await screen.findByLabelText("질문"), "8시 반까지 잠실 가야 해요");
    await user.click(screen.getByRole("button", { name: "조건 찾기" }));

    expect(await screen.findByText(PARSE_QUERY_TEXT.unavailable)).toBeInTheDocument();
    const parseCall = fetchMock.mock.calls.find(([, init]) => init?.method === "POST");
    expect(parseCall?.[1]?.body).toBe(JSON.stringify({ text: "8시 반까지 잠실 가야 해요" }));
  });

  it("빈 질문은 보내지 않는다", async () => {
    const fetchMock = mockQueryApi();
    const { user } = renderApp("/");

    await user.click(await screen.findByRole("button", { name: "조건 찾기" }));
    expect(await screen.findByText("질문을 적어 주세요.")).toBeInTheDocument();
    expect(requestedUrls(fetchMock).some((url) => url.pathname === "/api/v1/parse-query")).toBe(
      false,
    );
  });

  it("정류장 목록을 받지 못하면 연결 안내와 다시 시도를 보인다", async () => {
    mockFetch([{ path: "/api/v1/stations", networkError: true }]);
    renderApp("/");
    expect(await screen.findByText("인터넷 연결을 확인해 주세요.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "다시 시도" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "예보 보기" })).toBeDisabled();
  });
});
