import { skipToken, useQuery } from "@tanstack/react-query";
import { getStationRoutes } from "../../../api/endpoints";
import { queryKeys } from "../../../api/queryKeys";
import type { StationId } from "../../../api/types";

const ROUTES_STALE_TIME_MS = 10 * 60_000;

/** 정류장의 목적지·노선. 정류장을 고르기 전에는 부르지 않는다. */
export function useStationRoutes(stationId: StationId | null) {
  return useQuery({
    queryKey: queryKeys.stationRoutes(stationId ?? ""),
    queryFn: stationId ? ({ signal }) => getStationRoutes(stationId, signal) : skipToken,
    staleTime: ROUTES_STALE_TIME_MS,
  });
}
