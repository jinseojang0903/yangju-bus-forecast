import { queryOptions, useQueries } from "@tanstack/react-query";
import { getRoutePositions } from "../../../api/endpoints";
import { queryKeys } from "../../../api/queryKeys";
import type { RouteId } from "../../../api/types";
import { positionsRefreshDelayMs } from "../positions";

function routePositionsQuery(routeId: RouteId, isActive: boolean) {
  return queryOptions({
    queryKey: queryKeys.routePositions(routeId),
    queryFn: ({ signal }) => getRoutePositions(routeId, signal),
    // 보이는 노선만 받고 다시 부른다(같은 IP 호출 제한을 아끼려고). 다른 노선은 고를 때 받는다.
    enabled: isActive,
    refetchInterval: (query) => positionsRefreshDelayMs(query.state.data, query.state.error),
  });
}

/**
 * 노선별 최신 차량 위치(계약 4.8). 고른 노선(activeRouteId)만 조회하고,
 * 그 응답의 nextRefreshAt 에 맞춰 스냅샷과 따로 다시 조회한다.
 * 고르지 않은 노선은 부르지 않지만, 전에 받은 응답·오류(404 등)는 결과에 그대로 남아 노선 버튼 표시에 쓴다.
 * 다시 조회하는 동안에도 이전 응답을 그대로 둔다.
 */
export function useRoutePositions(routeIds: RouteId[], activeRouteId: RouteId | undefined) {
  return useQueries({
    queries: routeIds.map((routeId) => routePositionsQuery(routeId, routeId === activeRouteId)),
  });
}
