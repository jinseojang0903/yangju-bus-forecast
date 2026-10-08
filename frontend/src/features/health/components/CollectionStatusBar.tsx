import { cx } from "../../../lib/classNames";
import { COLLECTION_STATUS_LOADING, COLLECTION_STATUS_SEPARATOR } from "../../../lib/labels";
import {
  CLIENT_CLOCK_TICK_MS,
  type CollectionStatusTone,
  type CollectionStatusView,
  toCollectionStatusView,
  UNAVAILABLE_VIEW,
} from "../collectionStatus";
import { useClientClock } from "../hooks/useClientClock";
import { useHealth } from "../hooks/useHealth";
import styles from "./CollectionStatusBar.module.css";

/** 색만으로 구분하지 않도록 붙이는 기호(낭독기에는 숨긴다) */
const TONE_SYMBOL: Record<CollectionStatusTone, string> = {
  ok: "✓",
  warning: "!",
  idle: "i",
  unknown: "?",
};

const LOADING_VIEW: CollectionStatusView = {
  tone: "unknown",
  title: COLLECTION_STATUS_LOADING,
  detail: null,
};

/**
 * 수집 상태 한 줄(계약 4.1). 지금 보는 정보가 살아 있는 데이터인지 알려 준다.
 * 실패해도 화면 전체를 오류로 넘기지 않고 이 줄만 바꾼다. 이전 응답이 있으면 그것을 계속 보이되,
 * 다시 부르기가 실패하는 중이면 '상태 확인 지연'을 붙여 주의 표시로 바꾼다.
 * 낭독기는 상태 이름이 바뀔 때만 읽는다(경과 시간은 live 영역 밖).
 */
export function CollectionStatusBar() {
  const health = useHealth();
  // 새 응답을 받으면(dataUpdatedAt 변경) 기기 시계를 바로 다시 맞춘다
  const clientNowMs = useClientClock(CLIENT_CLOCK_TICK_MS, health.dataUpdatedAt);

  const view = health.data
    ? toCollectionStatusView({
        health: health.data,
        receivedAtMs: health.dataUpdatedAt,
        clientNowMs,
        isRefreshFailing: health.isRefetchError,
      })
    : health.isError
      ? UNAVAILABLE_VIEW
      : LOADING_VIEW;

  return (
    <div className={cx(styles.bar, styles[view.tone])} data-tone={view.tone}>
      <span aria-hidden="true" className={styles.symbol}>
        {TONE_SYMBOL[view.tone]}
      </span>
      <p className={styles.text}>
        <span role="status" className={styles.title}>
          {view.title}
        </span>
        {view.detail !== null && (
          <span className={styles.detail}>
            {COLLECTION_STATUS_SEPARATOR}
            {view.detail}
          </span>
        )}
      </p>
    </div>
  );
}
