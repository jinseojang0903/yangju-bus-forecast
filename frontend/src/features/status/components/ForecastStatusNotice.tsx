import type { ForecastStatus, Timestamp } from "../../../api/types";
import { Notice } from "../../../components/Notice";
import { FORECAST_STATUS_TEXT, LAST_UPDATED_LABEL } from "../../../lib/labels";
import { formatKstTime } from "../../../lib/time";

interface ForecastStatusNoticeProps {
  status: Exclude<ForecastStatus, "ok">;
  /** 정보 오래됨일 때 보여 줄 마지막 갱신 시각 */
  lastUpdatedAt: Timestamp | null;
}

/** 확률을 낼 수 없는 예보 상태(계약 5장)의 안내. 확률 자리에 놓는다. */
export function ForecastStatusNotice({ status, lastUpdatedAt }: ForecastStatusNoticeProps) {
  const text = FORECAST_STATUS_TEXT[status];
  const tone = status === "not_yet" || status === "outside_hours" ? "info" : "warning";
  return (
    <Notice tone={tone} title={text.title}>
      <p>{text.description}</p>
      {status === "stale" && (
        <p>
          {LAST_UPDATED_LABEL}{" "}
          <time dateTime={lastUpdatedAt ?? undefined}>{formatKstTime(lastUpdatedAt)}</time>
        </p>
      )}
    </Notice>
  );
}
