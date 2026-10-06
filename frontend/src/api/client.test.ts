import { describe, expect, it } from "vitest";
import { errorBody, mockFetch, requestedUrls } from "../test/mockFetch";
import { ApiError, apiGet, apiPost, buildUrl } from "./client";

async function catchError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("요청이 실패해야 하는데 성공했습니다");
}

describe("buildUrl", () => {
  it("빈 쿼리 값은 빼고 /api/v1 을 붙인다", () => {
    expect(
      buildUrl("/snapshot", {
        station: "235000392",
        destination: "jamsil",
        deadline: undefined,
        scenario: null,
      }),
    ).toBe("/api/v1/snapshot?station=235000392&destination=jamsil");
  });
});

describe("apiGet", () => {
  it("성공 응답을 그대로 돌려주고 쿼리를 붙여 보낸다", async () => {
    const fetchMock = mockFetch([{ path: "/api/v1/stations", body: { items: [] } }]);
    await expect(apiGet("/stations", { a: "1" }, { arrayFields: ["items"] })).resolves.toEqual({
      items: [],
    });
    expect(requestedUrls(fetchMock)[0]?.search).toBe("?a=1");
  });

  it("400 은 VALIDATION_FAILED 와 details 를 담는다", async () => {
    mockFetch([
      {
        path: "/api/v1/snapshot",
        status: 400,
        body: {
          error: {
            code: "VALIDATION_FAILED",
            message: "요청 형식이 올바르지 않습니다",
            details: [{ field: "deadline", reason: "HH:MM 형식(00:00–23:59)이어야 합니다" }],
          },
        },
      },
    ]);
    const error = await catchError(apiGet("/snapshot"));
    expect(error.code).toBe("VALIDATION_FAILED");
    expect(error.status).toBe(400);
    expect(error.details).toEqual([
      { field: "deadline", reason: "HH:MM 형식(00:00–23:59)이어야 합니다" },
    ]);
  });

  it("404 는 NOT_FOUND", async () => {
    mockFetch([
      {
        path: "/api/v1/stations/999/routes",
        status: 404,
        body: errorBody("NOT_FOUND", "리소스를 찾을 수 없습니다"),
      },
    ]);
    const error = await catchError(apiGet("/stations/999/routes"));
    expect(error.code).toBe("NOT_FOUND");
    expect(error.status).toBe(404);
  });

  it("429 는 RATE_LIMITED 와 Retry-After(초)를 담는다", async () => {
    mockFetch([
      {
        path: "/api/v1/snapshot",
        status: 429,
        headers: { "Retry-After": "12" },
        body: errorBody("RATE_LIMITED", "요청이 너무 많습니다"),
      },
    ]);
    const error = await catchError(apiGet("/snapshot"));
    expect(error.code).toBe("RATE_LIMITED");
    expect(error.retryAfterSec).toBe(12);
  });

  it("에러 본문이 형식에 맞지 않으면 HTTP 상태로 코드를 정한다", async () => {
    mockFetch([{ path: "/api/v1/snapshot", status: 502, body: "Bad Gateway" }]);
    const error = await catchError(apiGet("/snapshot"));
    expect(error.code).toBe("INTERNAL_ERROR");
    expect(error.status).toBe(502);
  });

  it("fetch 자체가 실패하면 NETWORK_ERROR", async () => {
    mockFetch([{ path: "/api/v1/stations", networkError: true }]);
    const error = await catchError(apiGet("/stations"));
    expect(error.code).toBe("NETWORK_ERROR");
    expect(error.status).toBe(0);
  });

  it("성공 응답이 JSON 이 아니면 INVALID_RESPONSE", async () => {
    mockFetch([{ path: "/api/v1/stations", body: "<html></html>" }]);
    const error = await catchError(apiGet("/stations"));
    expect(error.code).toBe("INVALID_RESPONSE");
  });

  it("arrayFields 가 배열이 아니면 INVALID_RESPONSE", async () => {
    mockFetch([{ path: "/api/v1/stations", body: { items: null } }]);
    const error = await catchError(apiGet("/stations", undefined, { arrayFields: ["items"] }));
    expect(error.code).toBe("INVALID_RESPONSE");
  });
});

describe("apiPost", () => {
  it("JSON 본문과 Content-Type 을 보낸다", async () => {
    const fetchMock = mockFetch([
      { method: "POST", path: "/api/v1/parse-query", body: { status: "unavailable" } },
    ]);
    await apiPost("/parse-query", { text: "잠실" });
    const init = fetchMock.mock.calls[0]?.[1];
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(JSON.stringify({ text: "잠실" }));
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
  });
});
