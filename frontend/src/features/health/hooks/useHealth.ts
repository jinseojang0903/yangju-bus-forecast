import { useQuery } from "@tanstack/react-query";
import { getHealth } from "../../../api/endpoints";
import { queryKeys } from "../../../api/queryKeys";
import { healthRefetchDelayMs } from "../collectionStatus";

/**
 * 수집 상태(계약 4.1). 30초마다 다시 부르고, 창이 보이지 않으면 멈춘다
 * (refetchIntervalInBackground 기본값 false). 다시 부르다 실패해도 이전 응답(data)은 남는다.
 */
export function useHealth() {
  return useQuery({
    queryKey: queryKeys.health(),
    queryFn: ({ signal }) => getHealth(signal),
    refetchInterval: (query) => healthRefetchDelayMs(query.state.error),
  });
}
