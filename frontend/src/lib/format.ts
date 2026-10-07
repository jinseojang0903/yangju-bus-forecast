// 숫자 표시 형식. 값은 서버 스냅샷 그대로이며 여기서는 표기만 바꾼다(다시 계산하지 않는다).

/** 부동소수 오차 보정(예: 0.29 * 100 = 28.999999999999996) */
const PERCENT_EPSILON = 1e-9;

/**
 * 0.75 → "75%". 반올림하지 않고 내림한다.
 * 반올림하면 0.695 가 "70%" 로 보이면서 등급은 '위험 보통'(70% 미만)이 되어 화면이 서로 어긋난다.
 */
export function formatPercent(probability: number): string {
  return `${Math.floor(probability * 100 + PERCENT_EPSILON)}%`;
}

/** 3 → "3석", null → "정보 없음" */
export function formatSeats(seats: number | null): string {
  return seats === null ? "정보 없음" : `${seats}석`;
}

/** 7 → "7분 후", 0 이하 → "곧 도착", null → "도착 예상 없음" */
export function formatMinutesToArrival(minutes: number | null): string {
  if (minutes === null) return "도착 예상 없음";
  if (minutes <= 0) return "곧 도착";
  return `${minutes}분 후`;
}
