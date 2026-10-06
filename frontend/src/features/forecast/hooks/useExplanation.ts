import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { getExplanation } from "../../../api/endpoints";
import { queryKeys } from "../../../api/queryKeys";
import type { SnapshotId } from "../../../api/types";

/**
 * 스냅샷 설명(계약 4.5). 숫자를 먼저 그린 뒤 부른다.
 * 같은 스냅샷의 설명은 바뀌지 않으므로 다시 받지 않고, 실패해도 재시도하지 않는다(호출 제한 분당 20회).
 * 스냅샷이 바뀌어도 자리가 스켈레톤으로 돌아가지 않도록 이전 설명을 placeholder 로 유지한다.
 * 이전 설명을 그대로 보여 줄지는 시점을 비교해 ExplanationPanel 이 정한다.
 */
export function useExplanation(snapshotId: SnapshotId) {
  return useQuery({
    queryKey: queryKeys.explanation(snapshotId),
    queryFn: ({ signal }) => getExplanation(snapshotId, signal),
    placeholderData: keepPreviousData,
    staleTime: Number.POSITIVE_INFINITY,
    retry: false,
    refetchOnWindowFocus: false,
  });
}
