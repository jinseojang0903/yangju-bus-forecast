import { QueryClient } from "@tanstack/react-query";
import { isApiError } from "./client";

const MAX_RETRIES = 2;

/**
 * 일시적인 실패(네트워크·서버 내부 오류)만 다시 시도한다.
 * 4xx(형식·없음·호출 제한)는 다시 보내도 결과가 같거나 제한을 더 깎으므로 재시도하지 않는다.
 */
export function shouldRetry(failureCount: number, error: unknown): boolean {
  if (!isApiError(error)) return false;
  if (error.code === "NETWORK_ERROR" || error.code === "INTERNAL_ERROR") {
    return failureCount < MAX_RETRIES;
  }
  return false;
}

export function createQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        retry: shouldRetry,
        staleTime: 5_000,
        // 기본값("online")은 오프라인이면 요청을 멈춰 로딩이 끝나지 않는다.
        // 실제로 보내 NETWORK_ERROR 를 받아야 '인터넷 연결 확인' 안내를 보일 수 있다.
        networkMode: "always",
      },
      mutations: {
        retry: false,
        networkMode: "always",
      },
    },
  });
}
