import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useClientClock } from "./useClientClock";

const START_MS = Date.parse("2026-10-08T07:31:00+09:00");

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(START_MS);
});

afterEach(() => {
  vi.useRealTimers();
});

describe("useClientClock", () => {
  it("tickMs 마다 기기 시각을 갱신한다", () => {
    const { result } = renderHook(() => useClientClock(15_000));
    expect(result.current).toBe(START_MS);

    act(() => {
      vi.advanceTimersByTime(14_999);
    });
    expect(result.current).toBe(START_MS);

    act(() => {
      vi.advanceTimersByTime(1);
    });
    expect(result.current).toBe(START_MS + 15_000);
  });

  it("resetKey 가 바뀌면 틱을 기다리지 않고 바로 다시 맞춘다", () => {
    const { result, rerender } = renderHook(({ key }) => useClientClock(15_000, key), {
      initialProps: { key: 1 },
    });
    vi.setSystemTime(START_MS + 7_000);
    rerender({ key: 2 });
    expect(result.current).toBe(START_MS + 7_000);
  });

  it("언마운트하면 타이머를 정리한다", () => {
    const { unmount } = renderHook(() => useClientClock(15_000));
    expect(vi.getTimerCount()).toBe(1);
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });
});
