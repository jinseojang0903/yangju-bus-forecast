import { useQuery } from "@tanstack/react-query";
import { getStations } from "../../../api/endpoints";
import { queryKeys } from "../../../api/queryKeys";

/** 기준정보라 자주 바뀌지 않는다 */
const STATIONS_STALE_TIME_MS = 10 * 60_000;

export function useStations() {
  return useQuery({
    queryKey: queryKeys.stations(),
    queryFn: ({ signal }) => getStations(signal),
    staleTime: STATIONS_STALE_TIME_MS,
  });
}
