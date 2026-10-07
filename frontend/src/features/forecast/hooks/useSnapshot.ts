import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getSnapshot, type SnapshotRequest } from "../../../api/endpoints";
import { queryKeys } from "../../../api/queryKeys";
import { errorRefreshDelayMs, snapshotRefreshDelayMs } from "../refresh";

/**
 * 예보 스냅샷. nextRefreshAt 에 맞춰 자동으로 다시 조회하고,
 * 조건이 바뀌거나 갱신 중이어도 이전 숫자를 지우지 않는다(계약 7장).
 */
export function useSnapshot(request: SnapshotRequest) {
  return useQuery({
    queryKey: queryKeys.snapshot(request),
    queryFn: ({ signal }) => getSnapshot(request, signal),
    placeholderData: keepPreviousData,
    refetchInterval: (query) => {
      const { data, error } = query.state;
      if (error) return errorRefreshDelayMs(error);
      if (!data) return false;
      return snapshotRefreshDelayMs({
        nextRefreshAt: data.nextRefreshAt,
        computedAt: data.computedAt,
      });
    },
  });
}
