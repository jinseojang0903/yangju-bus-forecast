import { act, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { queryKeys } from "../../../api/queryKeys";
import type { HealthResponse } from "../../../api/types";
import {
  COLLECTION_NO_RECORD,
  COLLECTION_STATUS_CHECK_DELAYED,
  COLLECTION_STATUS_TITLE,
  COLLECTION_STATUS_UNAVAILABLE,
} from "../../../lib/labels";
import { exampleStations } from "../../../test/fixtures";
import { errorBody, mockFetch, requestedUrls } from "../../../test/mockFetch";
import { renderApp, renderWithProviders } from "../../../test/render";
import { CollectionStatusBar } from "./CollectionStatusBar";

const HEALTH_PATH = "/api/v1/health";

const okHealth: HealthResponse = {
  status: "ok",
  now: "2026-10-08T07:31:20+09:00",
  lastSuccessAt: "2026-10-08T07:31:08+09:00",
};

function mockHealth(body: HealthResponse) {
  return mockFetch([{ path: HEALTH_PATH, body }]);
}

function healthCalls(fetchMock: ReturnType<typeof mockFetch>): number {
  return requestedUrls(fetchMock).filter((url) => url.pathname === HEALTH_PATH).length;
}

/** 줄 전체의 글자(기호 포함) */
function barText(): string {
  return screen.getByRole("status").closest("[data-tone]")?.textContent ?? "";
}

function barTone(): string | null {
  return screen.getByRole("status").closest("[data-tone]")?.getAttribute("data-tone") ?? null;
}

afterEach(() => {
  vi.useRealTimers();
});

describe("CollectionStatusBar", () => {
  it("ok: 1분 안이면 수집 정상 · 마지막 갱신 방금", async () => {
    mockHealth(okHealth);
    renderWithProviders(<CollectionStatusBar />);

    await screen.findByText(COLLECTION_STATUS_TITLE.ok);
    // 낭독 영역(role=status)에는 상태 이름만 있고, 바뀌는 경과 시간은 밖에 둔다
    expect(screen.getByRole("status")).toHaveTextContent(/^수집 정상$/);
    expect(screen.getByText(/마지막 갱신 방금/)).toBeInTheDocument();
    expect(barTone()).toBe("ok");
  });

  it("degraded: 색 말고도 기호와 '수집 지연' 문구로 알리고 N분 전으로 보인다", async () => {
    mockHealth({ ...okHealth, status: "degraded", lastSuccessAt: "2026-10-08T07:26:00+09:00" });
    renderWithProviders(<CollectionStatusBar />);

    expect(await screen.findByText(COLLECTION_STATUS_TITLE.degraded)).toBeInTheDocument();
    expect(screen.getByText(/마지막 갱신 5분 전/)).toBeInTheDocument();
    expect(barTone()).toBe("warning");
    expect(barText().startsWith("!")).toBe(true);
  });

  it("idle: 오늘이 아닌 마지막 수집은 날짜와 함께 보인다", async () => {
    mockHealth({
      status: "idle",
      now: "2026-10-08T05:00:00+09:00",
      lastSuccessAt: "2026-10-07T10:14:50+09:00",
    });
    renderWithProviders(<CollectionStatusBar />);

    expect(await screen.findByText(COLLECTION_STATUS_TITLE.idle)).toBeInTheDocument();
    expect(screen.getByText(/마지막 수집 10\/07 10:14/)).toBeInTheDocument();
    expect(barTone()).toBe("idle");
  });

  it("lastSuccessAt 이 없으면 수집 기록 없음", async () => {
    mockHealth({ ...okHealth, lastSuccessAt: null });
    renderWithProviders(<CollectionStatusBar />);

    expect(await screen.findByText(COLLECTION_NO_RECORD)).toBeInTheDocument();
    expect(screen.queryByText(/마지막 갱신/)).not.toBeInTheDocument();
  });

  it("요청이 실패하면 이 줄만 '확인할 수 없어요'로 바꾼다", async () => {
    mockFetch([{ path: HEALTH_PATH, networkError: true }]);
    renderWithProviders(<CollectionStatusBar />);

    expect(await screen.findByText(COLLECTION_STATUS_UNAVAILABLE)).toBeInTheDocument();
    expect(barTone()).toBe("unknown");
  });

  it("다시 부르기가 계속 실패하면 이전 값은 두고 '상태 확인 지연'을 붙여 주의 표시, 성공하면 되돌린다", async () => {
    mockHealth(okHealth);
    const { queryClient } = renderWithProviders(<CollectionStatusBar />);
    await screen.findByText(COLLECTION_STATUS_TITLE.ok);
    expect(barTone()).toBe("ok");

    const failingFetch = mockFetch([
      { path: HEALTH_PATH, status: 500, body: errorBody("INTERNAL_ERROR", "서버 오류") },
    ]);
    for (let attempt = 0; attempt < 2; attempt += 1) {
      await act(async () => {
        await queryClient.refetchQueries({ queryKey: queryKeys.health() });
      });
    }

    expect(healthCalls(failingFetch)).toBe(2);
    // 상태 이름(낭독 영역)은 그대로라 실패마다 다시 읽지 않는다
    expect(screen.getByRole("status")).toHaveTextContent(/^수집 정상$/);
    expect(
      screen.getByText(new RegExp(`마지막 갱신 방금 · ${COLLECTION_STATUS_CHECK_DELAYED}`)),
    ).toBeInTheDocument();
    expect(barTone()).toBe("warning");
    expect(barText().startsWith("!")).toBe(true);
    expect(screen.queryByText(COLLECTION_STATUS_UNAVAILABLE)).not.toBeInTheDocument();

    const recoveredFetch = mockHealth(okHealth);
    await act(async () => {
      await queryClient.refetchQueries({ queryKey: queryKeys.health() });
    });
    expect(healthCalls(recoveredFetch)).toBe(1);
    await waitFor(() => expect(barTone()).toBe("ok"));
    expect(screen.queryByText(new RegExp(COLLECTION_STATUS_CHECK_DELAYED))).not.toBeInTheDocument();
  });

  it("30초마다 다시 불러 상태 변화를 반영한다", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    const fetchMock = mockHealth(okHealth);
    renderWithProviders(<CollectionStatusBar />);
    await screen.findByText(COLLECTION_STATUS_TITLE.ok);
    expect(healthCalls(fetchMock)).toBe(1);

    const degradedFetch = mockHealth({
      ...okHealth,
      status: "degraded",
      now: "2026-10-08T07:33:20+09:00",
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30_000);
    });

    expect(healthCalls(degradedFetch)).toBe(1);
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent(COLLECTION_STATUS_TITLE.degraded),
    );
    expect(screen.getByText(/마지막 갱신 2분 전/)).toBeInTheDocument();
  });

  it("수집 상태를 못 받아도 조건 입력 화면은 그대로 보인다", async () => {
    mockFetch([
      { path: HEALTH_PATH, networkError: true },
      { path: "/api/v1/stations", body: exampleStations },
    ]);
    renderApp("/");

    expect(await screen.findByText(COLLECTION_STATUS_UNAVAILABLE)).toBeInTheDocument();
    expect(await screen.findByLabelText("출발 정류장")).toBeInTheDocument();
  });
});
