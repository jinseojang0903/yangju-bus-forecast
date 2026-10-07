import { screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import { renderWithProviders } from "../test/render";
import { ErrorMessage } from "./ErrorMessage";

describe("ErrorMessage", () => {
  it.each([
    ["VALIDATION_FAILED", 400, "조회 조건이 올바르지 않아요."],
    ["NOT_FOUND", 404, "정류장이나 목적지를 찾을 수 없어요."],
    ["RATE_LIMITED", 429, "요청이 너무 많아요."],
    ["NETWORK_ERROR", 0, "인터넷 연결을 확인해 주세요."],
    ["INTERNAL_ERROR", 500, "일시적인 문제로 정보를 불러오지 못했어요."],
  ] as const)("%s 는 정해진 문구를 보인다", async (code, status, title) => {
    renderWithProviders(
      <ErrorMessage error={new ApiError({ code, status, message: "서버 내부 메시지" })} />,
    );
    expect(await screen.findByText(title)).toBeInTheDocument();
    expect(screen.queryByText("서버 내부 메시지")).toBeNull();
  });

  it("400·404 는 조건을 다시 고르게 안내한다", async () => {
    renderWithProviders(
      <ErrorMessage error={new ApiError({ code: "NOT_FOUND", status: 404, message: "" })} />,
    );
    expect(await screen.findByRole("link", { name: "조건 다시 고르기" })).toHaveAttribute(
      "href",
      "/",
    );
  });

  it("429 는 Retry-After 초를 알려 주고 다시 시도할 수 있다", async () => {
    const onRetry = vi.fn();
    const { user } = renderWithProviders(
      <ErrorMessage
        error={new ApiError({ code: "RATE_LIMITED", status: 429, message: "", retryAfterSec: 12 })}
        onRetry={onRetry}
      />,
    );
    expect(await screen.findByText("12초 뒤에 다시 시도해 주세요.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "다시 시도" }));
    expect(onRetry).toHaveBeenCalledTimes(1);
  });

  it("자연어 질문의 400 은 길이 안내로 바꾼다", async () => {
    renderWithProviders(
      <ErrorMessage
        error={new ApiError({ code: "VALIDATION_FAILED", status: 400, message: "" })}
        context="parseQuery"
      />,
    );
    expect(await screen.findByText("질문은 1–200자로 적어 주세요.")).toBeInTheDocument();
  });
});
