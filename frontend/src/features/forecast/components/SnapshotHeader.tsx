import type { SnapshotResponse } from "../../../api/types";
import { formatKstTime } from "../../../lib/time";
import styles from "./SnapshotHeader.module.css";

interface SnapshotHeaderProps {
  snapshot: SnapshotResponse;
  isRefreshing: boolean;
}

/** 예보 화면 맨 위. 계산 시점(computedAt)을 항상 보인다(계약 7장). 스크롤해도 위에 붙어 있다. */
export function SnapshotHeader({ snapshot, isRefreshing }: SnapshotHeaderProps) {
  const isInService = snapshot.service.state === "in_service";
  return (
    <section className={styles.header} aria-label="조회 정보와 계산 시점">
      <p className={styles.route}>
        <strong>{snapshot.station.name}</strong> ({snapshot.station.directionLabel}) →{" "}
        <strong>{snapshot.destination.name}</strong>
        <span className={styles.deadline}>
          {snapshot.deadline ? ` · 도착 마감 ${snapshot.deadline}` : " · 도착 마감 없음"}
        </span>
      </p>
      <p className={styles.computedAt}>
        계산 시점 <time dateTime={snapshot.computedAt}>{formatKstTime(snapshot.computedAt)}</time>
      </p>
      <p className={styles.refresh} aria-live="polite">
        {isRefreshing
          ? "새 정보를 확인하는 중…"
          : isInService
            ? `다음 갱신 ${formatKstTime(snapshot.nextRefreshAt)}`
            : "예보 시간이 되면 자동으로 갱신해요"}
      </p>
    </section>
  );
}
