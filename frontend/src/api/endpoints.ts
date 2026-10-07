import { apiGet, apiPost } from "./client";
import type {
  ExplanationResponse,
  ParseQueryRequest,
  ParseQueryResponse,
  RouteId,
  RoutePositionsResponse,
  SnapshotId,
  SnapshotQuery,
  SnapshotResponse,
  StationId,
  StationRoutesResponse,
  StationsResponse,
} from "./types";

/** GET /stations (4.3) */
export function getStations(signal?: AbortSignal): Promise<StationsResponse> {
  return apiGet<StationsResponse>("/stations", undefined, { signal, arrayFields: ["items"] });
}

/** GET /stations/{stationId}/routes (4.3). 404: 정류장 없음, 400: ID 형식 */
export function getStationRoutes(
  stationId: StationId,
  signal?: AbortSignal,
): Promise<StationRoutesResponse> {
  return apiGet<StationRoutesResponse>(
    `/stations/${encodeURIComponent(stationId)}/routes`,
    undefined,
    {
      signal,
      arrayFields: ["destinations"],
    },
  );
}

export interface SnapshotRequest extends SnapshotQuery {
  /**
   * 계약 12장 개발용 scenario. 개발 빌드에서만 채운다(features/forecast/snapshotRequest.ts).
   * 운영 빌드에서는 항상 null 이라 서버로 보내지 않는다.
   */
  scenario?: string | null;
}

/** GET /snapshot (4.4). 400: 필수 누락·형식, 404: 정류장·목적지 없음 */
export function getSnapshot(
  request: SnapshotRequest,
  signal?: AbortSignal,
): Promise<SnapshotResponse> {
  return apiGet<SnapshotResponse>(
    "/snapshot",
    {
      station: request.station,
      destination: request.destination,
      deadline: request.deadline,
      scenario: request.scenario,
    },
    { signal, arrayFields: ["buses"] },
  );
}

/** GET /snapshot/{snapshotId}/explanation (4.5). 404: 스냅샷 없음·만료, 429 */
export function getExplanation(
  snapshotId: SnapshotId,
  signal?: AbortSignal,
): Promise<ExplanationResponse> {
  return apiGet<ExplanationResponse>(
    `/snapshot/${encodeURIComponent(snapshotId)}/explanation`,
    undefined,
    { signal },
  );
}

/** GET /routes/{routeId}/positions (4.8). 404: 지원하지 않는 노선, 400: ID 형식 */
export function getRoutePositions(
  routeId: RouteId,
  signal?: AbortSignal,
): Promise<RoutePositionsResponse> {
  return apiGet<RoutePositionsResponse>(
    `/routes/${encodeURIComponent(routeId)}/positions`,
    undefined,
    { signal, arrayFields: ["stations", "vehicles"] },
  );
}

/** POST /parse-query (4.6). 400: 빈 문자열·200자 초과, 429 */
export function postParseQuery(text: string): Promise<ParseQueryResponse> {
  const body: ParseQueryRequest = { text };
  return apiPost<ParseQueryResponse>("/parse-query", body);
}
