// docs/api-contract.md v2 의 타입을 1:1 로 옮긴 것이다. 계약이 바뀌면 이 파일을 먼저 고친다.
// 필드 이름·값을 프론트에서 임의로 바꾸지 않는다.

// ── 2. 공통 타입 ──────────────────────────────────────────────
/** ISO 8601 + "+09:00" */
export type Timestamp = string;
/** "HH:MM" (KST) */
export type TimeOfDay = string;
/** GBIS 정류소 ID, ASCII 숫자 1–20자리 */
export type StationId = string;
/** GBIS 노선 ID, ASCII 숫자 1–20자리 */
export type RouteId = string;
/** GBIS vehId, ASCII 숫자 */
export type VehicleId = string;
/** 자체 코드, ^[a-z_]{1,32}$ */
export type DestinationId = string;
/** uuid. 클라이언트는 해석하지 않는다 */
export type SnapshotId = string;

export type LeadTimeMin = 5 | 10 | 15;
export type RiskLevel = "high" | "medium" | "low";

// ── 5. 상태값 ─────────────────────────────────────────────────
export type ForecastStatus =
  | "ok"
  | "not_yet"
  | "insufficient_cases"
  | "not_validated"
  | "stale"
  | "missing_input"
  | "outside_hours";

export type AlternativeStatus =
  | "recommended"
  | "no_alternative"
  | "arrival_unavailable"
  | "undecidable";

export type ServiceState = "in_service" | "outside_collection" | "outside_forecast_hours";

// ── 1.4 공통 에러 형식 ────────────────────────────────────────
export type ApiErrorCode =
  | "VALIDATION_FAILED"
  | "NOT_FOUND"
  | "METHOD_NOT_ALLOWED"
  | "RATE_LIMITED"
  | "INTERNAL_ERROR";

export interface ApiErrorDetail {
  field: string;
  reason: string;
}

export interface ApiErrorBody {
  error: {
    code: ApiErrorCode;
    message: string;
    details: ApiErrorDetail[];
  };
}

// ── 4.1 GET /health ───────────────────────────────────────────
export interface HealthResponse {
  status: "ok" | "degraded" | "idle";
  now: Timestamp;
  lastSuccessAt: Timestamp | null;
}

// ── 4.3 GET /stations, GET /stations/{stationId}/routes ───────
export interface Station {
  stationId: StationId;
  name: string;
  directionLabel: string;
  mobileNo: string | null;
}

export interface StationsResponse {
  items: Station[];
}

export interface DestinationRoute {
  routeId: RouteId;
  routeName: string;
  alightStationId: StationId;
  alightStationName: string;
}

export interface Destination {
  destinationId: DestinationId;
  name: string;
  routes: DestinationRoute[];
}

export interface StationRoutesResponse {
  stationId: StationId;
  destinations: Destination[];
}

// ── 4.4 GET /snapshot ─────────────────────────────────────────
export interface SnapshotQuery {
  station: StationId;
  destination: DestinationId;
  deadline?: TimeOfDay;
}

export type BusSource = "arrival_1st" | "arrival_2nd" | "timetable_next";

export type ArrivalEstimateSource = "predict_time_sec" | "predict_time_min" | "timetable";

export interface ForecastInputs {
  seats: number | null;
  headwayMin: number | null;
}

export interface Forecast {
  leadTimeMin: LeadTimeMin;
  status: ForecastStatus;
  issuedAt: Timestamp | null;
  noSeatProbability: number | null;
  n: number | null;
  k: number | null;
  riskLevel: RiskLevel | null;
  preliminary: boolean;
  inputs: ForecastInputs;
}

export interface Bus {
  routeId: RouteId;
  routeName: string;
  vehicleId: VehicleId | null;
  plateNo: string | null;
  source: BusSource;
  stationArrivalAt: Timestamp | null;
  arrivalEstimateSource: ArrivalEstimateSource | null;
  minutesToArrival: number | null;
  inForecastHours: boolean;
  currentSeats: number | null;
  seatsUpdatedAt: Timestamp | null;
  selectedLeadTimeMin: LeadTimeMin | null;
  forecasts: Forecast[];
}

export type RecommendationReason = "lowest_risk" | "earliest_among_equal_risk";

export interface RecommendedAlternative {
  routeId: RouteId;
  vehicleId: VehicleId | null;
  reasonCode: RecommendationReason;
}

export interface AlternativeCandidate {
  routeId: RouteId;
  routeName: string;
  vehicleId: VehicleId | null;
  source: BusSource;
  stationArrivalAt: Timestamp | null;
  destinationArrivalAt: Timestamp | null;
  meetsDeadline: boolean | null;
  leadTimeMin: LeadTimeMin | null;
  noSeatProbability: number | null;
  riskLevel: RiskLevel | null;
}

export interface Alternatives {
  status: AlternativeStatus;
  recommended: RecommendedAlternative | null;
  switchSuggested: boolean;
  candidates: AlternativeCandidate[];
}

export interface ServiceInfo {
  state: ServiceState;
  message: string | null;
  nextForecastStartAt: Timestamp | null;
}

export interface SnapshotResponse {
  snapshotId: SnapshotId;
  computedAt: Timestamp;
  nextRefreshAt: Timestamp;
  rulesVersion: string;
  dataUpdatedAt: Timestamp | null;
  stale: boolean;
  service: ServiceInfo;
  station: { stationId: StationId; name: string; directionLabel: string };
  destination: { destinationId: DestinationId; name: string };
  deadline: TimeOfDay | null;
  walkMinutesAllowed: number;
  buses: Bus[];
  alternatives: Alternatives;
}

// ── 4.5 GET /snapshot/{snapshotId}/explanation ────────────────
export type LlmFallbackReason =
  | "llm_disabled"
  | "timeout"
  | "check_failed"
  | "daily_limit"
  | "error";

export interface ExplanationResponse {
  snapshotId: SnapshotId;
  computedAt: Timestamp;
  source: "llm" | "fallback";
  fallbackReason: LlmFallbackReason | null;
  text: string;
}

// ── 4.6 POST /parse-query ─────────────────────────────────────
export interface ParseQueryRequest {
  text: string;
}

export interface ParseQueryResponse {
  status: "parsed" | "need_more" | "unavailable";
  fallbackReason: LlmFallbackReason | null;
  query: {
    station: StationId | null;
    destination: DestinationId | null;
    deadline: TimeOfDay | null;
  };
  message: string;
}
