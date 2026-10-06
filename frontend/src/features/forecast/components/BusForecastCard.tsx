import type { Bus, Timestamp } from "../../../api/types";
import { PreliminaryBadge } from "../../../components/PreliminaryBadge";
import { RiskBadge } from "../../../components/RiskBadge";
import { cx } from "../../../lib/classNames";
import { formatMinutesToArrival, formatPercent, formatSeats } from "../../../lib/format";
import {
  ARRIVAL_ESTIMATE_SOURCE_TEXT,
  formatCases,
  leadTimeLabel,
  NO_SEAT_PROBABILITY_LABEL,
  RISK_TERM,
} from "../../../lib/labels";
import { formatKstHourMinute } from "../../../lib/time";
import { ForecastStatusNotice } from "../../status/components/ForecastStatusNotice";
import { type ProbabilityView, toBusForecastView } from "../busForecast";
import styles from "./BusForecastCard.module.css";

interface BusForecastCardProps {
  bus: Bus;
  /** 스냅샷 전체의 stale. true 면 확률 대신 현재 잔여석을 보인다 */
  isSnapshotStale: boolean;
  dataUpdatedAt: Timestamp | null;
}

/** 버스 한 대의 도착 예상·현재 잔여석과, 서버가 고른 선행시간 예보(확률 또는 상태 안내) */
export function BusForecastCard({ bus, isSnapshotStale, dataUpdatedAt }: BusForecastCardProps) {
  const view = toBusForecastView(bus, isSnapshotStale);
  const arrivalSourceText = bus.arrivalEstimateSource
    ? ARRIVAL_ESTIMATE_SOURCE_TEXT[bus.arrivalEstimateSource]
    : null;

  return (
    <article className={styles.card} aria-label={`${bus.routeName} 버스`}>
      <div className={styles.top}>
        <h3 className={styles.routeName}>{bus.routeName}</h3>
        <p className={styles.arrival}>
          <strong>{formatMinutesToArrival(bus.minutesToArrival)}</strong>
          {bus.stationArrivalAt && (
            <span> · {formatKstHourMinute(bus.stationArrivalAt)} 도착 예정</span>
          )}
        </p>
        {arrivalSourceText && <span className={styles.sourceTag}>{arrivalSourceText}</span>}
      </div>

      {view.kind === "probability" ? (
        <ProbabilityBlock view={view} />
      ) : (
        <ForecastStatusNotice status={view.status} lastUpdatedAt={dataUpdatedAt} />
      )}

      <p className={cx(styles.seats, view.kind === "status" && styles.seatsEmphasis)}>
        지금 잔여석 <strong>{formatSeats(bus.currentSeats)}</strong>
        {bus.seatsUpdatedAt && (
          <span className={styles.muted}> ({formatKstHourMinute(bus.seatsUpdatedAt)} 기준)</span>
        )}
      </p>
    </article>
  );
}

function ProbabilityBlock({ view }: { view: ProbabilityView }) {
  return (
    <div className={cx(styles.risk, styles[view.riskLevel])}>
      <p className={styles.riskHeading}>
        <span className={styles.riskTerm}>{RISK_TERM}</span>
        <RiskBadge level={view.riskLevel} />
        {view.preliminary && <PreliminaryBadge />}
      </p>
      <p className={styles.cases}>
        비슷한 과거 운행 <strong>{formatCases(view.n, view.k)}</strong>가 도착 때 0석이었어요
      </p>
      <p className={styles.meta}>
        {NO_SEAT_PROBABILITY_LABEL} {formatPercent(view.noSeatProbability)} ·{" "}
        {leadTimeLabel(view.leadTimeMin)}
        {view.issuedAt && ` (${formatKstHourMinute(view.issuedAt)})`}
      </p>
    </div>
  );
}
