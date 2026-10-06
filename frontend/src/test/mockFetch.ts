import { vi } from "vitest";

export interface MockRoute {
  method?: "GET" | "POST";
  /** 쿼리를 뺀 경로. 예: "/api/v1/snapshot" */
  path: string;
  status?: number;
  /** 객체면 JSON 으로, 문자열이면 그대로 보낸다 */
  body?: unknown;
  headers?: Record<string, string>;
  /** true 면 fetch 가 TypeError 를 던진다(네트워크 끊김) */
  networkError?: boolean;
}

function toUrl(input: RequestInfo | URL): URL {
  const raw = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
  return new URL(raw, "http://localhost");
}

/**
 * 전역 fetch 를 경로별 고정 응답으로 바꾼다. 등록하지 않은 경로는 404 NOT_FOUND 로 답한다.
 * 정리는 src/test/setup.ts 의 vi.unstubAllGlobals() 가 한다.
 */
export function mockFetch(routes: MockRoute[]) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = toUrl(input);
    const method = (init?.method ?? "GET").toUpperCase();
    const route = routes.find(
      (candidate) => (candidate.method ?? "GET") === method && candidate.path === url.pathname,
    );
    if (!route) {
      return new Response(
        JSON.stringify({
          error: { code: "NOT_FOUND", message: "리소스를 찾을 수 없습니다", details: [] },
        }),
        { status: 404, headers: { "Content-Type": "application/json" } },
      );
    }
    if (route.networkError) {
      throw new TypeError("Failed to fetch");
    }
    const body =
      route.body === undefined
        ? null
        : typeof route.body === "string"
          ? route.body
          : JSON.stringify(route.body);
    return new Response(body, {
      status: route.status ?? 200,
      headers: { "Content-Type": "application/json", ...route.headers },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

/** fetchMock 이 받은 요청 URL 목록 */
export function requestedUrls(fetchMock: ReturnType<typeof mockFetch>): URL[] {
  return fetchMock.mock.calls.map(([input]) => toUrl(input));
}

export function errorBody(code: string, message: string) {
  return { error: { code, message, details: [] } };
}
