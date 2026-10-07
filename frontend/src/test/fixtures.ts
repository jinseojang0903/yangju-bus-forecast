import type {
  AlternativeStatus,
  ExplanationResponse,
  ForecastStatus,
  ParseQueryResponse,
  RoutePositionsResponse,
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

// 계약 4.8 형식의 G1300 노선 위치(좌표·차량은 시험용으로 만든 값이다).
// 정류장 1–8 잠실행(5번이 내 정류장), 9–10 귀로.
export const examplePositions: RoutePositionsResponse = {
  routeId: "235000092",
  routeName: "G1300",
  targetStationId: "235000392",
  computedAt: "2026-10-07T07:31:20+09:00",
  dataUpdatedAt: "2026-10-07T07:31:10+09:00",
  stale: false,
  inCollectionWindow: true,
  nextRefreshAt: "2026-10-07T07:31:33+09:00",
  stations: [
    {
      stationSeq: 1,
      stationId: "235000001",
      name: "기점",
      lat: 37.81,
      lng: 127.04,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 2,
      stationId: "235000002",
      name: "고읍지구",
      lat: 37.8,
      lng: 127.05,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 3,
      stationId: "235000003",
      name: "고읍중앙",
      lat: 37.79,
      lng: 127.06,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 4,
      stationId: "235000004",
      name: "고읍마을",
      lat: 37.78,
      lng: 127.07,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 5,
      stationId: "235000392",
      name: "덕현초교",
      lat: 37.77,
      lng: 127.08,
      isOutbound: true,
      isTarget: true,
    },
    {
      stationSeq: 6,
      stationId: "235000006",
      name: "덕정사거리",
      lat: 37.76,
      lng: 127.09,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 7,
      stationId: "235000007",
      name: "고속도로입구",
      lat: 37.7,
      lng: 127.1,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 8,
      stationId: "123000001",
      name: "잠실역",
      lat: 37.51,
      lng: 127.1,
      isOutbound: true,
      isTarget: false,
    },
    {
      stationSeq: 9,
      stationId: "123000002",
      name: "잠실역 귀로",
      lat: 37.512,
      lng: 127.102,
      isOutbound: false,
      isTarget: false,
    },
    {
      stationSeq: 10,
      stationId: "235000010",
      name: "덕현초교 귀로",
      lat: 37.771,
      lng: 127.081,
      isOutbound: false,
      isTarget: false,
    },
  ],
  vehicles: [
    {
      vehicleId: "235000901",
      plateNo: "경기76바0001",
      stationSeq: 1,
      stationId: "235000001",
      state: "arrived",
      remainSeats: 0,
      stopsToTarget: 4,
    },
    {
      vehicleId: "235000902",
      plateNo: "경기76바0002",
      stationSeq: 3,
      stationId: "235000003",
      state: "departed",
      remainSeats: 5,
      stopsToTarget: 2,
    },
    {
      vehicleId: "235000903",
      plateNo: null,
      stationSeq: 4,
      stationId: "235000004",
      state: "arrived",
      remainSeats: null,
      stopsToTarget: 1,
    },
    {
      vehicleId: "235000904",
      plateNo: "경기76바0004",
      stationSeq: 7,
      stationId: "235000007",
      state: "passing",
      remainSeats: 10,
      stopsToTarget: null,
    },
    {
      vehicleId: "235000905",
      plateNo: "경기76바0005",
      stationSeq: 9,
      stationId: "123000002",
      state: "unknown",
      remainSeats: null,
      stopsToTarget: null,
    },
  ],
};

export function clonePositions(
  positions: RoutePositionsResponse = examplePositions,
): RoutePositionsResponse {
  return JSON.parse(JSON.stringify(positions)) as RoutePositionsResponse;
}
