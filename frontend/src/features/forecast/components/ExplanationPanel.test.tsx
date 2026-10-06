import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { ExplanationResponse } from "../../../api/types";
import { EXPLANATION_LOADING, EXPLANATION_TIME_MISMATCH } from "../../../lib/labels";
import { ExplanationPanel } from "./ExplanationPanel";

const FIRST = { snapshotId: "snap-1", computedAt: "2026-10-07T07:31:20+09:00" };
const SECOND = { snapshotId: "snap-2", computedAt: "2026-10-07T07:31:33+09:00" };

function explanation(
  base: { snapshotId: string; computedAt: string },
  text: string,
): ExplanationResponse {
  return { ...base, source: "fallback", fallbackReason: "llm_disabled", text };
}

function jsonResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
  });
}

/** 첫 설명은 바로 주고, 두 번째 설명은 resolveSecond 를 부를 때까지 붙잡아 둔다 */
function stubExplanationFetch(first: ExplanationResponse) {
  let resolveSecond: (body: ExplanationResponse) => void = () => {};
  vi.stubGlobal(
    "fetch",
    vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes(FIRST.snapshotId)) return Promise.resolve(jsonResponse(first));
      return new Promise<Response>((resolve) => {
        resolveSecond = (body) => resolve(jsonResponse(body));
      });
    }),
  );
  return { resolveSecond: (body: ExplanationResponse) => resolveSecond(body) };
}

function renderPanel(props: { snapshotId: string; computedAt: string }) {
  const queryClient = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: Number.POSITIVE_INFINITY } },
  });
  const ui = (next: { snapshotId: string; computedAt: string }) => (
    <QueryClientProvider client={queryClient}>
      <ExplanationPanel {...next} />
    </QueryClientProvider>
  );
  const view = render(ui(props));
  return {
    rerender: (next: { snapshotId: string; computedAt: string }) => view.rerender(ui(next)),
  };
}

describe("ExplanationPanel", () => {
  it("스냅샷이 바뀌면 이전 설명을 숨기고 조용히 준비 중으로 둔 뒤 새 설명을 붙인다", async () => {
    const fetchControl = stubExplanationFetch(explanation(FIRST, "첫 설명"));
    const { rerender } = renderPanel(FIRST);
    expect(await screen.findByText("첫 설명")).toBeInTheDocument();

    rerender(SECOND);
    expect(screen.getByText(EXPLANATION_LOADING)).toBeInTheDocument();
    expect(screen.queryByText("첫 설명")).toBeNull();
    expect(screen.queryByText(EXPLANATION_TIME_MISMATCH)).toBeNull();

    await act(async () => {
      fetchControl.resolveSecond(explanation(SECOND, "둘째 설명"));
    });
    expect(await screen.findByText("둘째 설명")).toBeInTheDocument();
    expect(screen.queryByText(EXPLANATION_TIME_MISMATCH)).toBeNull();
  });

  it("새로 받은 설명의 시점이 화면과 다를 때만 불일치 안내를 띄운다", async () => {
    const fetchControl = stubExplanationFetch(explanation(FIRST, "첫 설명"));
    const { rerender } = renderPanel(FIRST);
    await screen.findByText("첫 설명");

    rerender(SECOND);
    await act(async () => {
      fetchControl.resolveSecond(
        explanation({ ...SECOND, computedAt: "2026-10-07T07:30:00+09:00" }, "어긋난 설명"),
      );
    });
    expect(await screen.findByText(EXPLANATION_TIME_MISMATCH)).toBeInTheDocument();
    expect(screen.queryByText("어긋난 설명")).toBeNull();
  });
});
