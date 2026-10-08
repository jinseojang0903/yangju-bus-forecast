// 화면 문구는 이 파일 한 곳에 둔다(CLAUDE.md '바꾸지 않는 규칙', 계약 5장).
// 위험 표현은 '무좌석 위험'. '탑승 여유', '만차 가능' 같은 옛 문구는 쓰지 않는다.
// 에러 문구만 예외로 components/ErrorMessage.tsx 에 둔다(.claude/rules/frontend.md).
import type {
  AlternativeStatus,
  ArrivalEstimateSource,
  ExplanationResponse,
  ForecastStatus,
  HealthResponse,
  ParseQueryResponse,
  RecommendationReason,
  RiskLevel,
  ServiceState,
  VehicleState,
} from "../api/types";
import type { Elapsed } from "./time";

export interface StatusText {
  title: string;
  description: string;
}

// ── 위험 ──────────────────────────────────────────────────────
export const RISK_TERM = "무좌석 위험";

/** high: p ≥ 0.7, medium: 0.3 ≤ p < 0.7, low: p < 0.3 (등급은 서버가 정한다) */
export const RISK_LABEL: Record<RiskLevel, string> = {
  high: "위험 높음",
  medium: "위험 보통",
  low: "위험 낮음",
};

/** 색 말고도 모양으로 구분하기 위한 기호(화면 낭독기에는 숨긴다) */
export const RISK_SYMBOL: Record<RiskLevel, string> = {
  high: "▲",
  medium: "◆",
  low: "▽",
};

/** "24회 중 18회" — 서버의 n, k 를 그대로 쓴다 */
export function formatCases(n: number, k: number): string {
  return `${n}회 중 ${k}회`;
}

export const NO_SEAT_PROBABILITY_LABEL = "0석 확률";

export function leadTimeLabel(leadTimeMin: number): string {
  return `도착 ${leadTimeMin}분 전 예보`;
}

// ── 예비 ──────────────────────────────────────────────────────
export const PRELIMINARY_LABEL = "예비";
export const PRELIMINARY_HINT = "공개 기준 판정 전이라 예비 수치예요";

// ── 예보 상태 7종 (계약 5장) ──────────────────────────────────
export const FORECAST_STATUS_TEXT: Record<ForecastStatus, StatusText> = {
  ok: {
    title: "예보 제공",
    description: "비슷한 과거 운행에서 도착할 때 0석이었던 비율이에요.",
  },
  not_yet: {
    title: "아직 예보 시점 전",
    description: "버스가 도착 15분 전이 되면 예보가 나와요. 지금은 현재 잔여석만 보여 드려요.",
  },
  insufficient_cases: {
    title: "사례 부족",
    description: "비슷한 과거 운행이 20회 미만이라 확률을 내지 않았어요. 현재 잔여석을 참고하세요.",
  },
  not_validated: {
    title: "검증 미통과",
    description:
      "이 예보 시점은 공개 기준을 통과하지 못해 확률을 보여 드리지 않아요. 현재 잔여석을 참고하세요.",
  },
  stale: {
    title: "정보 오래됨",
    description: "수집 정보가 늦게 들어와 예보를 계산하지 않았어요. 현재 잔여석을 참고하세요.",
  },
  missing_input: {
    title: "입력 누락",
    description: "예보 시점의 잔여석이나 앞차 간격을 계산할 수 없었어요. 현재 잔여석을 참고하세요.",
  },
  outside_hours: {
    title: "예보 시간이 아니에요",
    description: "06:00–08:59에 도착하는 버스만 예보해요. 현재 잔여석을 참고하세요.",
  },
};

export const LAST_UPDATED_LABEL = "마지막 갱신";

/** 스냅샷 전체가 오래됐을 때(stale: true) 상단 안내 */
export const SNAPSHOT_STALE_TEXT: StatusText = {
  title: "정보 오래됨",
  description: "수집 정보가 늦어지고 있어요. 확률 대신 현재 잔여석을 보여 드려요.",
};

// ── 대안 상태 4종 (계약 5장·6장) ──────────────────────────────
export const ALTERNATIVE_STATUS_TEXT: Record<AlternativeStatus, StatusText> = {
  recommended: {
    title: "추천",
    description: "마감 안에 도착하는 차 중 무좌석 위험이 가장 낮은 차를 골랐어요.",
  },
  no_alternative: {
    title: "대안 없음",
    description: "마감 시각 안에 목적지에 도착하는 후보가 없어요.",
  },
  arrival_unavailable: {
    title: "도착시각 미제공",
    description: "구간 소요 기록이 아직 부족해 목적지 도착 시각을 낼 수 없어요.",
  },
  undecidable: {
    title: "판단 불가",
    description: "후보나 위험 정보가 부족해 추천할 수 없어요.",
  },
};

export const RECOMMENDATION_REASON_TEXT: Record<RecommendationReason, string> = {
  lowest_risk: "무좌석 위험이 가장 낮아요.",
  earliest_among_equal_risk: "위험이 같은 차 중 가장 먼저 와요.",
};

export const RECOMMENDED_BADGE = "추천";
export const SWITCH_SUGGESTION_TITLE = "바꿔 타기 제안";

/** 노선 이름을 모르면(후보·버스를 찾지 못함) 이름 없이 자연스러운 문장으로 쓴다 */
export function switchSuggestionText(
  currentRouteName: string | null,
  recommendedRouteName: string | null,
): string {
  const current = currentRouteName
    ? `지금 오는 차(${currentRouteName})는 ${RISK_LABEL.high}이에요.`
    : `지금 오는 차는 ${RISK_LABEL.high}이에요.`;
  const recommended = recommendedRouteName
    ? `추천 차(${recommendedRouteName})를 타면 ${RISK_TERM}이 더 낮아요.`
    : `${RISK_TERM}이 더 낮은 추천 차를 아래에서 확인해 주세요.`;
  return `${current} ${recommended}`;
}

/** 스냅샷이 오래됐을 때(stale: true) 대안 비교 영역 안내. 추천·바꿔 타기 제안은 숨긴다 */
export const ALTERNATIVES_STALE_TEXT: StatusText = {
  title: "정보 오래됨",
  description: "수집 정보가 늦어 추천을 잠시 멈췄어요. 정보가 갱신되면 다시 비교해요.",
};

export const DEADLINE_TEXT = {
  meets: "마감 안",
  misses: "마감 넘음",
  unknown: "마감 판단 불가",
  noDeadline: "마감 미입력",
} as const;

export const DESTINATION_ARRIVAL_UNAVAILABLE = "도착시각 미제공";
export const NO_PROBABILITY_TEXT = "확률 없음";

// ── 서비스 시간 (계약 2.2절) ──────────────────────────────────
export const SERVICE_OUTSIDE_TITLE = "지금은 예보 시간이 아니에요";

export const SERVICE_STATE_TEXT: Record<Exclude<ServiceState, "in_service">, string> = {
  outside_collection: "예보는 평일 아침 06:00–08:59에 도착하는 버스만 해요.",
  outside_forecast_hours: "지금 예보할 버스가 없어요. 06:00–08:59에 도착하는 버스만 예보해요.",
};

export const NEXT_FORECAST_START_LABEL = "다음 예보 시작";
export const NEXT_FORECAST_START_UNKNOWN = "다음 예보 시작 시각을 아직 알 수 없어요.";

// ── 수집 상태 줄 (계약 4.1, 모든 화면 상단) ───────────────────
/** 상태 이름. 화면 낭독기는 이 부분이 바뀔 때만 읽는다(경과 시간은 읽지 않는다) */
export const COLLECTION_STATUS_TITLE: Record<HealthResponse["status"], string> = {
  ok: "수집 정상",
  degraded: "수집 지연",
  idle: "수집 시간 아님(평일 05:30–10:15)",
};
export const COLLECTION_NO_RECORD = "수집 기록 없음";
export const COLLECTION_STATUS_UNAVAILABLE = "수집 상태를 확인할 수 없어요";
export const COLLECTION_STATUS_LOADING = "수집 상태를 확인하는 중…";
export const COLLECTION_STATUS_SEPARATOR = " · ";
/** 이전 응답은 있는데 다시 부르기가 실패하고 있을 때 세부 뒤에 붙인다 */
export const COLLECTION_STATUS_CHECK_DELAYED = "상태 확인 지연";

/** "마지막 갱신 방금" / "마지막 갱신 3분 전" (ok·degraded) */
export function lastUpdatedAgoText(agoText: string): string {
  return `${LAST_UPDATED_LABEL} ${agoText}`;
}

/** "방금", "N분 전", "N시간 전". 단위 판정은 lib/time.ts 의 toElapsed */
export function elapsedAgoText(elapsed: Elapsed): string {
  switch (elapsed.unit) {
    case "justNow":
      return "방금";
    case "minute":
      return `${elapsed.value}분 전`;
    case "hour":
      return `${elapsed.value}시간 전`;
  }
}

/** "마지막 수집 10:14" 또는 "마지막 수집 10/07 10:14" (idle) */
export function lastCollectedAtText(time: string): string {
  return `마지막 수집 ${time}`;
}

// ── 도착 예상 출처 ────────────────────────────────────────────
/** predict_time_sec 는 기본이라 표시하지 않는다 */
export const ARRIVAL_ESTIMATE_SOURCE_TEXT: Record<ArrivalEstimateSource, string | null> = {
  predict_time_sec: null,
  predict_time_min: "도착 예상(분 단위)",
  timetable: "시간표 기준",
};

// ── 설명(LLM) ─────────────────────────────────────────────────
export const EXPLANATION_SOURCE_TEXT: Record<ExplanationResponse["source"], string> = {
  llm: "AI 설명",
  fallback: "기본 안내",
};
export const EXPLANATION_LOADING = "설명을 준비하고 있어요";
export const EXPLANATION_UNAVAILABLE = "설명을 불러오지 못했어요. 위의 숫자를 기준으로 봐 주세요.";
export const EXPLANATION_TIME_MISMATCH =
  "설명의 기준 시점이 화면과 달라 보여 드리지 않아요. 다음 갱신을 기다려 주세요.";

// ── 자연어 질문 (계약 4.6) ────────────────────────────────────
export const PARSE_QUERY_TEXT: Record<ParseQueryResponse["status"], string> = {
  parsed: "질문에서 조건을 찾아 채웠어요. 확인한 뒤 '예보 보기'를 눌러 주세요.",
  need_more: "조건을 더 알려 주세요.",
  unavailable: "말로 묻기는 아직 준비 중이에요. 위에서 정류장과 목적지를 직접 골라 주세요.",
};

// ── 노선 지도 (계약 4.8) ──────────────────────────────────────
export const ROUTE_MAP_HEADING = "노선 지도";
export const ROUTE_MAP_ROUTE_PICKER_LABEL = "지도에 보일 노선";
export const ROUTE_MAP_LOADING = "지도를 불러오는 중…";
export const ROUTE_MAP_REFRESHING = "위치를 새로 받는 중…";
export const ROUTE_MAP_RECENTER = "내 정류장 중심으로";
export const ROUTE_MAP_NO_STATIONS = "이 노선의 정류장 위치 정보가 아직 없어요.";
export const ROUTE_MAP_NO_ROUTES = "지도에 보일 노선이 없어요.";
export const ROUTE_MAP_NO_RECORD = "수집 기록 없음";
/** 400·404 로 지도를 받지 못한 노선 버튼에 붙인다(색만으로 구분하지 않게) */
export const ROUTE_MAP_ROUTE_UNAVAILABLE = "지도 없음";

/** "07:31:10 수집 기준" */
export function collectedAtText(time: string): string {
  return `${time} 수집 기준`;
}

export const ROUTE_MAP_STALE_TEXT: StatusText = {
  title: "정보 오래됨",
  description: "차량 위치가 늦게 들어오고 있어요. 지도는 마지막으로 받은 위치예요.",
};

export const ROUTE_MAP_OUTSIDE_COLLECTION_TEXT: StatusText = {
  title: "지금은 수집 시간이 아니에요",
  description: "마지막 수집 기준 위치예요. 수집은 평일 05:30–10:15에 해요.",
};

export function routeMapAriaLabel(routeName: string): string {
  return `${routeName} 노선 지도`;
}

/** 잔여석 null 은 "정보 없음"이고 0석과 다르다(계약 4.8) */
export function remainSeatsText(remainSeats: number | null): string {
  return remainSeats === null ? "잔여석 정보 없음" : `잔여 ${remainSeats}석`;
}

/** 지도 툴팁의 차량 상태. GBIS stateCd 를 짧게 옮긴다 */
export function vehicleStateText(state: VehicleState, stationName: string | null): string {
  const place = stationName ?? "정류장";
  switch (state) {
    case "arrived":
      return `${place} 도착`;
    case "departed":
      return `${place} 출발`;
    case "passing":
      // stateCd 0(교차로 통과). 정류장에 서 있지 않고 정류장 사이를 달리는 중이다
      return "정류장 사이 이동 중";
    case "unknown":
      return `${place} 부근`;
  }
}

/** "덕현초교까지 3정류장". 0 이면 내 정류장에 있는 차 */
export function stopsToTargetText(targetName: string, stopsToTarget: number): string {
  return stopsToTarget <= 0
    ? `${targetName} 도착·통과 중`
    : `${targetName}까지 ${stopsToTarget}정류장`;
}

export const APPROACHING_HEADING = "내 정류장으로 오는 차량";
export const APPROACHING_EMPTY = "지금 내 정류장으로 오는 차량이 없어요.";
export const MY_STATION_FALLBACK_NAME = "내 정류장";
