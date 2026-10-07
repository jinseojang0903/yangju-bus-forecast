import type { Timestamp } from "../../../api/types";
import { Notice } from "../../../components/Notice";
import { LAST_UPDATED_LABEL, SNAPSHOT_STALE_TEXT } from "../../../lib/labels";
import { formatKstTime } from "../../../lib/time";

/** 스냅샷의 stale: true. 수집 데이터가 오래되어 확률 대신 현재 잔여석을 보여 준다는 안내 */
export function SnapshotStaleNotice({ dataUpdatedAt }: { dataUpdatedAt: Timestamp | null }) {
  return (
    <Notice tone="warning" title={SNAPSHOT_STALE_TEXT.title}>
      <p>{SNAPSHOT_STALE_TEXT.description}</p>
      <p>
        {LAST_UPDATED_LABEL}{" "}
        <time dateTime={dataUpdatedAt ?? undefined}>{formatKstTime(dataUpdatedAt)}</time>
      </p>
    </Notice>
  );
}
