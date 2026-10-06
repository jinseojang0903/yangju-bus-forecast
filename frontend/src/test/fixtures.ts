import type {
  AlternativeStatus,
  ExplanationResponse,
  ForecastStatus,
  ParseQueryResponse,
  SnapshotResponse,
  StationRoutesResponse,
  StationsResponse,
} from "../api/types";

// 계약 9장 예시 응답 그대로(형식 예시이며 실제 결과가 아니다).
export const exampleSnapshot: SnapshotResponse = {
  snapshotId: "0d6f3c1e-8a52-4f0b-9c2a-5d1f0e7b2a11",
  computedAt: "2026-10-07T07:31:20+09:00",
  nextRefreshAt: "2026-10-07T07:31:33+09:00",
  rulesVersion: "2026-10-06.1",
  dataUpdatedAt: "2026-10-07T07:31:10+09:00",
  stale: false,
  service: { state: "in_service", message: null, nextForecastStartAt: null },
  station: { stationId: "235000392", name: "덕현초교.덕고개", directionLabel: "잠실행" },
  destination: { destinationId: "jamsil", name: "잠실" },
  deadline: "08:30",
  walkMinutesAllowed: 0,
  buses: [
    {
      routeId: "235000092",
      routeName: "G1300",
      vehicleId: "235000359",
      plateNo: "경기76바8260",
      source: "arrival_1st",
      stationArrivalAt: "2026-10-07T07:38:40+09:00",
      arrivalEstimateSource: "predict_time_sec",
      minutesToArrival: 7,
      inForecastHours: true,
      currentSeats: 3,
      seatsUpdatedAt: "2026-10-07T07:31:10+09:00",
      selectedLeadTimeMin: 10,
      forecasts: [
        {
          leadTimeMin: 15,
          status: "ok",
          issuedAt: "2026-10-07T07:23:40+09:00",
          noSeatProbability: 0.62,
          n: 26,
          k: 16,
          riskLevel: "medium",
          preliminary: true,
          inputs: { seats: 7, headwayMin: 11 },
        },
        {
          leadTimeMin: 10,
          status: "ok",
          issuedAt: "2026-10-07T07:28:40+09:00",
          noSeatProbability: 0.75,
          n: 24,
          k: 18,
          riskLevel: "high",
          preliminary: true,
          inputs: { seats: 4, headwayMin: 11 },
        },
        {
          leadTimeMin: 5,
          status: "not_yet",
          issuedAt: null,
          noSeatProbability: null,
          n: null,
          k: null,
          riskLevel: null,
          preliminary: true,
          inputs: { seats: null, headwayMin: null },
        },
      ],
    },
    {
      routeId: "235000123",
      routeName: "1306",
      vehicleId: "235010110",
      plateNo: "경기76바8309",
      source: "arrival_2nd",
      stationArrivalAt: "2026-10-07T07:42:00+09:00",
      arrivalEstimateSource: "predict_time_sec",
      minutesToArrival: 10,
      inForecastHours: true,
      currentSeats: 14,
      seatsUpdatedAt: "2026-10-07T07:31:10+09:00",
      selectedLeadTimeMin: 15,
      forecasts: [
        {
          leadTimeMin: 15,
          status: "ok",
          issuedAt: "2026-10-07T07:27:00+09:00",
          noSeatProbability: 0.2,
          n: 25,
          k: 5,
          riskLevel: "low",
          preliminary: true,
          inputs: { seats: 15, headwayMin: 14 },
        },
        {
          leadTimeMin: 10,
          status: "not_yet",
          issuedAt: null,
          noSeatProbability: null,
          n: null,
          k: null,
          riskLevel: null,
          preliminary: true,
          inputs: { seats: null, headwayMin: null },
        },
        {
          leadTimeMin: 5,
          status: "not_yet",
          issuedAt: null,
          noSeatProbability: null,
          n: null,
          k: null,
          riskLevel: null,
          preliminary: true,
          inputs: { seats: null, headwayMin: null },
        },
      ],
    },
  ],
  alternatives: {
    status: "recommended",
    recommended: { routeId: "235000123", vehicleId: "235010110", reasonCode: "lowest_risk" },
    switchSuggested: true,
    candidates: [
      {
        routeId: "235000092",
        routeName: "G1300",
        vehicleId: "235000359",
        source: "arrival_1st",
        stationArrivalAt: "2026-10-07T07:38:40+09:00",
        destinationArrivalAt: "2026-10-07T08:21:00+09:00",
        meetsDeadline: true,
        leadTimeMin: 10,
        noSeatProbability: 0.75,
        riskLevel: "high",
      },
      {
        routeId: "235000123",
        routeName: "1306",
        vehicleId: "235010110",
        source: "arrival_2nd",
        stationArrivalAt: "2026-10-07T07:42:00+09:00",
        destinationArrivalAt: "2026-10-07T08:27:00+09:00",
        meetsDeadline: true,
        leadTimeMin: 15,
        noSeatProbability: 0.2,
        riskLevel: "low",
      },
    ],
  },
};

export function cloneSnapshot(snapshot: SnapshotResponse = exampleSnapshot): SnapshotResponse {
  return JSON.parse(JSON.stringify(snapshot)) as SnapshotResponse;
}

/** 첫 버스(G1300)의 선택된 예보 상태를 바꾼 스냅샷. ok 가 아니면 확률·n·k·등급을 비운다 */
export function snapshotWithSelectedStatus(status: ForecastStatus): SnapshotResponse {
  const snapshot = cloneSnapshot();
  const bus = snapshot.buses[0];
  if (status === "not_yet") {
    bus.selectedLeadTimeMin = null;
    return snapshot;
  }
  for (const forecast of bus.forecasts) {
    if (forecast.leadTimeMin !== bus.selectedLeadTimeMin) continue;
    forecast.status = status;
    if (status !== "ok") {
      forecast.noSeatProbability = null;
      forecast.n = null;
      forecast.k = null;
      forecast.riskLevel = null;
    }
  }
  return snapshot;
}

/** 계약 9장 끝: 수집 시간 밖 */
export function outsideCollectionSnapshot(): SnapshotResponse {
  const snapshot = cloneSnapshot();
  snapshot.nextRefreshAt = "2026-10-08T05:45:00+09:00";
  snapshot.service = {
    state: "outside_collection",
    message: "지금은 예보 시간이 아니에요",
    nextForecastStartAt: "2026-10-08T05:45:00+09:00",
  };
  snapshot.buses = [];
  snapshot.alternatives = {
    status: "undecidable",
    recommended: null,
    switchSuggested: false,
    candidates: [],
  };
  return snapshot;
}

/** 대안 상태를 바꾼 스냅샷(계약 6장 규칙에 맞게 후보 값도 바꾼다) */
export function snapshotWithAlternativeStatus(status: AlternativeStatus): SnapshotResponse {
  const snapshot = cloneSnapshot();
  const alternatives = snapshot.alternatives;
  if (status === "recommended") return snapshot;

  alternatives.status = status;
  alternatives.recommended = null;
  alternatives.switchSuggested = false;
  for (const candidate of alternatives.candidates) {
    if (status === "no_alternative") {
      candidate.meetsDeadline = false;
    } else if (status === "arrival_unavailable") {
      candidate.destinationArrivalAt = null;
      candidate.meetsDeadline = null;
    } else {
      candidate.leadTimeMin = null;
      candidate.noSeatProbability = null;
      candidate.riskLevel = null;
    }
  }
  return snapshot;
}

export const exampleStations: StationsResponse = {
  items: [
    {
      stationId: "235000392",
      name: "덕현초교.덕고개",
      directionLabel: "잠실행",
      mobileNo: null,
    },
  ],
};

export const exampleStationRoutes: StationRoutesResponse = {
  stationId: "235000392",
  destinations: [
    {
      destinationId: "jamsil",
      name: "잠실",
      routes: [
        {
          routeId: "235000092",
          routeName: "G1300",
          alightStationId: "123000001",
          alightStationName: "잠실역",
        },
        {
          routeId: "235000123",
          routeName: "1306",
          alightStationId: "123000001",
          alightStationName: "잠실역",
        },
      ],
    },
  ],
};

export const exampleExplanation: ExplanationResponse = {
  snapshotId: exampleSnapshot.snapshotId,
  computedAt: exampleSnapshot.computedAt,
  source: "fallback",
  fallbackReason: "llm_disabled",
  text: "고정 안내 문구입니다.",
};

export const unavailableParseQuery: ParseQueryResponse = {
  status: "unavailable",
  fallbackReason: "llm_disabled",
  query: { station: null, destination: null, deadline: null },
  message: "지금은 자연어 질문을 처리할 수 없습니다",
};
