import type { Timestamp } from "../api/types";

// 서버 시각은 "+09:00" 오프셋을 담고 있지만, 휴대폰의 시간대가 KST 가 아닐 수도 있으므로
// 표시할 때는 항상 Asia/Seoul 로 바꾼다. 로케일별 표기 차이를 피하려고 formatToParts 로 직접 조립한다.
const KST_FORMATTER = new Intl.DateTimeFormat("en-US", {
  timeZone: "Asia/Seoul",
  hourCycle: "h23",
  year: "numeric",
  month: "numeric",
  day: "numeric",
  weekday: "short",
  hour: "2-digit",
  minute: "2-digit",
  second: "2-digit",
});

const WEEKDAY_KO: Record<string, string> = {
  Sun: "일",
  Mon: "월",
  Tue: "화",
  Wed: "수",
  Thu: "목",
  Fri: "금",
  Sat: "토",
};

/** 시각을 알 수 없을 때 보이는 표시 */
export const UNKNOWN_TIME = "--:--";

interface KstParts {
  month: number;
  day: number;
  weekday: string;
  hour: string;
  minute: string;
  second: string;
}

function toKstParts(timestamp: Timestamp): KstParts | null {
  const date = new Date(timestamp);
  if (Number.isNaN(date.getTime())) return null;
  const parts: Record<string, string> = {};
  for (const part of KST_FORMATTER.formatToParts(date)) {
    parts[part.type] = part.value;
  }
  return {
    month: Number(parts.month),
    day: Number(parts.day),
    weekday: WEEKDAY_KO[parts.weekday ?? ""] ?? "",
    hour: pad2(parts.hour),
    minute: pad2(parts.minute),
    second: pad2(parts.second),
  };
}

function pad2(value: string | undefined): string {
  return (value ?? "").padStart(2, "0");
}

/** "07:31:20" (KST). 계산 시점처럼 초까지 보여야 하는 곳에 쓴다. */
export function formatKstTime(timestamp: Timestamp | null): string {
  const parts = timestamp ? toKstParts(timestamp) : null;
  if (!parts) return `${UNKNOWN_TIME}:--`;
  return `${parts.hour}:${parts.minute}:${parts.second}`;
}

/** "07:38" (KST) */
export function formatKstHourMinute(timestamp: Timestamp | null): string {
  const parts = timestamp ? toKstParts(timestamp) : null;
  if (!parts) return UNKNOWN_TIME;
  return `${parts.hour}:${parts.minute}`;
}

/** "10월 8일(목) 05:45" (KST) */
export function formatKstDateTime(timestamp: Timestamp | null): string {
  const parts = timestamp ? toKstParts(timestamp) : null;
  if (!parts) return UNKNOWN_TIME;
  return `${parts.month}월 ${parts.day}일(${parts.weekday}) ${parts.hour}:${parts.minute}`;
}

/** 두 시각이 같은 순간인지(표기 차이는 무시). 하나라도 해석할 수 없으면 false */
export function isSameInstant(a: Timestamp, b: Timestamp): boolean {
  const left = Date.parse(a);
  const right = Date.parse(b);
  return !Number.isNaN(left) && left === right;
}
