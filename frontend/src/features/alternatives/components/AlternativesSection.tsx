import { useId } from "react";
import type {
  AlternativeCandidate,
  Alternatives,
  Forecast,
  SnapshotResponse,
  TimeOfDay,
} from "../../../api/types";
import { Notice } from "../../../components/Notice";
import { PreliminaryBadge } from "../../../components/PreliminaryBadge";
import { RiskBadge } from "../../../components/RiskBadge";
import { cx } from "../../../lib/classNames";
import { formatPercent } from "../../../lib/format";
import {
  ALTERNATIVE_STATUS_TEXT,
  ALTERNATIVES_STALE_TEXT,
  DEADLINE_TEXT,
  DESTINATION_ARRIVAL_UNAVAILABLE,
  FORECAST_STATUS_TEXT,
  formatCases,
  leadTimeLabel,
  NO_PROBABILITY_TEXT,
  NO_SEAT_PROBABILITY_LABEL,
  RECOMMENDATION_REASON_TEXT,
  RECOMMENDED_BADGE,
  RISK_TERM,
  SWITCH_SUGGESTION_TITLE,
  switchSuggestionText,
} from "../../../lib/labels";
import { formatKstHourMinute } from "../../../lib/time";
import { busKey } from "../../forecast/busForecast";
import { findCandidateForecast, isRecommendedCandidate } from "../candidates";
import styles from "./AlternativesSection.module.css";

/** 대안 비교(계약 4.4 alternatives, 6장). 추천·마감·0석 위험은 서버 값 그대로 보인다. */
export function AlternativesSection({ snapshot }: { snapshot: SnapshotResponse }) {
  const headingId = useId();
  const { alternatives, buses, deadline, destination } = snapshot;
  const recommendedCandidate =
    alternatives.candidates.find((candidate) =>
      isRecommendedCandidate(candidate, alternatives.recommended),
    ) ?? null;
  const currentBus = buses[0] ?? null;
  // 수집 정보가 오래되면 추천 근거(0석 확률)를 믿을 수 없으므로 추천·바꿔 타기 제안을 숨긴다.
  const isStale = snapshot.stale;

  return (
    <section className={styles.section} aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.heading}>
        대안 비교
      </h2>
      <p className={styles.deadline}>
        {deadline ? `도착 마감 ${deadline} 기준` : "도착 마감 없이 비교해요"}
      </p>

      {!isStale && alternatives.switchSuggested && (
        <Notice tone="highlight" title={SWITCH_SUGGESTION_TITLE}>
          <p>
            {switchSuggestionText(
              currentBus?.routeName ?? null,
              recommendedCandidate?.routeName ?? null,
            )}
          </p>
        </Notice>
      )}

      {isStale ? (
        <Notice tone="warning" title={ALTERNATIVES_STALE_TEXT.title}>
          <p>{ALTERNATIVES_STALE_TEXT.description}</p>
        </Notice>
      ) : (
        <AlternativeStatusNotice alternatives={alternatives} />
      )}

      {alternatives.candidates.length > 0 && (
        <ul className={styles.list}>
          {alternatives.candidates.map((candidate, index) => (
            <CandidateItem
              // biome-ignore lint/suspicious/noArrayIndexKey: vehicleId 가 없는 후보끼리 노선·출처가 겹칠 수 있어 순번을 섞는다. 목록은 매 응답 통째로 바뀌므로 순번 키로 생기는 상태 꼬임이 없다
              key={`${busKey(candidate)}-${index}`}
              candidate={candidate}
              isRecommended={
                !isStale && isRecommendedCandidate(candidate, alternatives.recommended)
              }
              forecast={findCandidateForecast(candidate, buses)}
              deadline={deadline}
              destinationName={destination.name}
              isSnapshotStale={snapshot.stale}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function AlternativeStatusNotice({ alternatives }: { alternatives: Alternatives }) {
  const text = ALTERNATIVE_STATUS_TEXT[alternatives.status];
  if (alternatives.status === "recommended") {
    return (
      <Notice tone="info" title={text.title}>
        <p>{text.description}</p>
        {alternatives.recommended && (
          <p>{RECOMMENDATION_REASON_TEXT[alternatives.recommended.reasonCode]}</p>
        )}
      </Notice>
    );
  }
  return (
    <Notice tone="warning" title={text.title}>
      <p>{text.description}</p>
    </Notice>
  );
}

interface CandidateItemProps {
  candidate: AlternativeCandidate;
  isRecommended: boolean;
  /** 같은 버스의 같은 선행시간 예보(예비 여부·n·k 표시용). 찾지 못하면 null */
  forecast: Forecast | null;
  deadline: TimeOfDay | null;
  destinationName: string;
  isSnapshotStale: boolean;
}

function CandidateItem({
  candidate,
  isRecommended,
  forecast,
  deadline,
  destinationName,
  isSnapshotStale,
}: CandidateItemProps) {
  return (
    <li className={cx(styles.candidate, isRecommended && styles.recommended)}>
      <div className={styles.candidateTop}>
        <h3 className={styles.routeName}>{candidate.routeName}</h3>
        {isRecommended && (
          <span className={styles.recommendedBadge}>
            <span aria-hidden="true">★</span>
            <span>{RECOMMENDED_BADGE}</span>
          </span>
        )}
      </div>
      <dl className={styles.facts}>
        <div className={styles.fact}>
          <dt>정류장 도착</dt>
          <dd>{formatKstHourMinute(candidate.stationArrivalAt)}</dd>
        </div>
        <div className={styles.fact}>
          <dt>{destinationName} 도착</dt>
          <dd>
            {candidate.destinationArrivalAt
              ? formatKstHourMinute(candidate.destinationArrivalAt)
              : DESTINATION_ARRIVAL_UNAVAILABLE}
          </dd>
        </div>
        <div className={styles.fact}>
          <dt>마감</dt>
          <dd>
            <DeadlineResult meetsDeadline={candidate.meetsDeadline} deadline={deadline} />
          </dd>
        </div>
        <div className={styles.fact}>
          <dt>{RISK_TERM}</dt>
          <dd>
            <CandidateRisk
              candidate={candidate}
              forecast={forecast}
              isSnapshotStale={isSnapshotStale}
            />
          </dd>
        </div>
      </dl>
    </li>
  );
}

function DeadlineResult({
  meetsDeadline,
  deadline,
}: {
  meetsDeadline: boolean | null;
  deadline: TimeOfDay | null;
}) {
  if (meetsDeadline === true) {
    return (
      <span>
        <span aria-hidden="true">○ </span>
        {DEADLINE_TEXT.meets}
      </span>
    );
  }
  if (meetsDeadline === false) {
    return (
      <span className={styles.missed}>
        <span aria-hidden="true">✕ </span>
        {DEADLINE_TEXT.misses}
      </span>
    );
  }
  return <span>{deadline ? DEADLINE_TEXT.unknown : DEADLINE_TEXT.noDeadline}</span>;
}

function CandidateRisk({
  candidate,
  forecast,
  isSnapshotStale,
}: {
  candidate: AlternativeCandidate;
  forecast: Forecast | null;
  isSnapshotStale: boolean;
}) {
  const forecastStatus = forecast?.status ?? null;
  const { riskLevel, noSeatProbability } = candidate;
  if (isSnapshotStale || riskLevel === null || noSeatProbability === null) {
    let reason: string | null = null;
    if (isSnapshotStale) reason = FORECAST_STATUS_TEXT.stale.title;
    else if (forecastStatus !== null && forecastStatus !== "ok") {
      reason = FORECAST_STATUS_TEXT[forecastStatus].title;
    }
    return (
      <span className={styles.noProbability}>
        {NO_PROBABILITY_TEXT}
        {reason !== null && ` · ${reason}`}
      </span>
    );
  }
  // 후보에는 preliminary 가 없다. 같은 예보를 찾지 못하면 '예비' 를 빼지 않고 붙인다(보수적으로).
  const isPreliminary = forecast?.preliminary ?? true;
  const n = forecast?.n ?? null;
  const k = forecast?.k ?? null;
  return (
    <span className={styles.riskLine}>
      <RiskBadge level={riskLevel} />
      {isPreliminary && <PreliminaryBadge />}
      <span>
        {NO_SEAT_PROBABILITY_LABEL} {formatPercent(noSeatProbability)}
      </span>
      {n !== null && k !== null && <span>{formatCases(n, k)}</span>}
      {candidate.leadTimeMin !== null && (
        <span className={styles.muted}>{leadTimeLabel(candidate.leadTimeMin)}</span>
      )}
    </span>
  );
}
