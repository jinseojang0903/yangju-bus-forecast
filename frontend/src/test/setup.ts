import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach, vi } from "vitest";

// globals 를 켜지 않았으므로 Testing Library 자동 정리가 돌지 않는다. 직접 정리한다.
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
