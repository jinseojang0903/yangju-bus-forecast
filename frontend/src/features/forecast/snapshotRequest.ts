import type { SnapshotRequest } from "../../api/endpoints";
import type { SnapshotQuery } from "../../api/types";

/**
 * /forecast 주소의 쿼리를 /snapshot 요청으로 바꾼다. station·destination 이 없으면 null.
 * 형식 검증은 서버가 한다(400 VALIDATION_FAILED → 화면 안내).
 *
 * scenario(계약 12장)는 개발 빌드(isDev)에서만 그대로 넘긴다. 운영 빌드에서는 주소에 있어도 버린다.
 */
export function readSnapshotRequest(
  params: URLSearchParams,
  isDev: boolean,
): SnapshotRequest | null {
  const station = params.get("station")?.trim() ?? "";
  const destination = params.get("destination")?.trim() ?? "";
  if (!station || !destination) return null;
  const deadline = params.get("deadline")?.trim() || undefined;
  const scenario = isDev ? params.get("scenario") || null : null;
  return { station, destination, deadline, scenario };
}

/** 조건 입력 → 예보 화면 주소의 쿼리 문자열("?" 제외) */
export function toForecastSearch(query: SnapshotQuery): string {
  const params = new URLSearchParams({ station: query.station, destination: query.destination });
  if (query.deadline) params.set("deadline", query.deadline);
  return params.toString();
}
