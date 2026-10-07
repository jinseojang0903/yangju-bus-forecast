import { useId } from "react";
import { Link, useSearchParams } from "react-router";
import type { SnapshotRequest } from "../../../api/endpoints";
import type { SnapshotResponse } from "../../../api/types";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { Notice } from "../../../components/Notice";
import { AlternativesSection } from "../../alternatives/components/AlternativesSection";
import { ServiceNotice } from "../../status/components/ServiceNotice";
import { SnapshotStaleNotice } from "../../status/components/SnapshotStaleNotice";
import { busKey } from "../busForecast";
import { useSnapshot } from "../hooks/useSnapshot";
import { readSnapshotRequest } from "../snapshotRequest";
import { BusForecastCard } from "./BusForecastCard";
import { ExplanationPanel } from "./ExplanationPanel";
import styles from "./ForecastPage.module.css";
import { SnapshotHeader } from "./SnapshotHeader";

/** /forecast?station=&destination=&deadline= (개발 빌드에서는 &scenario= 도 받는다) */
export function ForecastPage() {
  const [searchParams] = useSearchParams();
  const request = readSnapshotRequest(searchParams, import.meta.env.DEV);

  if (!request) {
    return (
      <Notice tone="warning" title="조회 조건이 없어요">
        <p>출발 정류장과 목적지를 먼저 골라 주세요.</p>
        <Link to="/">조건 고르기</Link>
      </Notice>
    );
  }
  return <ForecastView request={request} />;
}

function ForecastView({ request }: { request: SnapshotRequest }) {
  const { data: snapshot, error, isPending, isFetching, refetch } = useSnapshot(request);
  const retry = () => {
    void refetch();
  };

  if (isPending) {
    return (
      <p className={styles.loading} aria-live="polite">
        예보를 불러오는 중…
      </p>
    );
  }
  if (!snapshot) {
    return (
      <>
        <BackLink />
        <ErrorMessage error={error} onRetry={retry} />
      </>
    );
  }

  const showAlternatives =
    snapshot.service.state === "in_service" || snapshot.alternatives.candidates.length > 0;

  return (
    <div className={styles.page}>
      <BackLink />
      <SnapshotHeader snapshot={snapshot} isRefreshing={isFetching} />
      {/* 갱신이 실패해도 이전 숫자는 그대로 두고 위에 안내만 띄운다 */}
      {error && <ErrorMessage error={error} onRetry={retry} compact />}
      <ServiceNotice service={snapshot.service} />
      {snapshot.stale && <SnapshotStaleNotice dataUpdatedAt={snapshot.dataUpdatedAt} />}
      <BusList snapshot={snapshot} />
      <ExplanationPanel snapshotId={snapshot.snapshotId} computedAt={snapshot.computedAt} />
      {showAlternatives && <AlternativesSection snapshot={snapshot} />}
      <p className={styles.footnote}>
        숫자는 과거 비슷한 운행의 실제 결과 비율이며, 탑승을 보장하지 않아요. 규칙 버전{" "}
        {snapshot.rulesVersion}
      </p>
    </div>
  );
}

function BusList({ snapshot }: { snapshot: SnapshotResponse }) {
  const headingId = useId();
  if (snapshot.buses.length === 0) {
    // 수집 시간 밖은 ServiceNotice 가 이미 설명한다.
    if (snapshot.service.state === "outside_collection") return null;
    return <p className={styles.empty}>지금 도착 예정인 버스가 없어요.</p>;
  }
  return (
    <section className={styles.buses} aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.heading}>
        도착 예정 버스
      </h2>
      <ul className={styles.busList}>
        {snapshot.buses.map((bus) => (
          <li key={busKey(bus)}>
            <BusForecastCard
              bus={bus}
              isSnapshotStale={snapshot.stale}
              dataUpdatedAt={snapshot.dataUpdatedAt}
            />
          </li>
        ))}
      </ul>
    </section>
  );
}

function BackLink() {
  return (
    <Link to="/" className={styles.back}>
      ← 조건 바꾸기
    </Link>
  );
}
