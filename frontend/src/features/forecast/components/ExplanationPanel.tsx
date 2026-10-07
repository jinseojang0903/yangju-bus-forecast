import { type ReactNode, useId } from "react";
import type { ExplanationResponse, SnapshotId, Timestamp } from "../../../api/types";
import {
  EXPLANATION_LOADING,
  EXPLANATION_SOURCE_TEXT,
  EXPLANATION_TIME_MISMATCH,
  EXPLANATION_UNAVAILABLE,
} from "../../../lib/labels";
import { isSameInstant } from "../../../lib/time";
import { useExplanation } from "../hooks/useExplanation";
import styles from "./ExplanationPanel.module.css";

interface ExplanationPanelProps {
  snapshotId: SnapshotId;
  computedAt: Timestamp;
}

/**
 * 숫자를 그린 뒤 뒤늦게 붙는 설명(계약 4.5·7장).
 * 자리 높이를 CSS 로 미리 잡아 두어, 설명이 들어와도 아래 내용이 밀리지 않는다.
 * 설명의 시점이 화면의 계산 시점과 다르면 보여 주지 않는다(LLM 출력 검사 ③과 같은 기준).
 * - 새 스냅샷의 설명을 기다리는 동안(이전 설명이 placeholder): 시점이 같으면 이전 설명을 그대로,
 *   다르면 '준비 중'으로 조용히 둔다. 이때는 시점 불일치 안내를 띄우지 않는다.
 * - 새로 받은 설명의 시점이 실제로 어긋날 때만 불일치 안내를 띄운다.
 */
export function ExplanationPanel({ snapshotId, computedAt }: ExplanationPanelProps) {
  const headingId = useId();
  const { data, error, isPending, isPlaceholderData } = useExplanation(snapshotId);

  let body: ReactNode;
  if (isPlaceholderData) {
    body =
      data && isSameInstant(data.computedAt, computedAt) ? (
        <ExplanationText text={data.text} source={data.source} />
      ) : (
        <p className={styles.muted}>{EXPLANATION_LOADING}</p>
      );
  } else if (isPending) {
    body = (
      <div className={styles.skeleton}>
        <span className={styles.visuallyHidden}>{EXPLANATION_LOADING}</span>
        <span aria-hidden="true" className={styles.line} />
        <span aria-hidden="true" className={styles.line} />
        <span aria-hidden="true" className={styles.lineShort} />
      </div>
    );
  } else if (error || !data) {
    body = <p className={styles.muted}>{EXPLANATION_UNAVAILABLE}</p>;
  } else if (data.snapshotId !== snapshotId || !isSameInstant(data.computedAt, computedAt)) {
    body = <p className={styles.muted}>{EXPLANATION_TIME_MISMATCH}</p>;
  } else {
    body = <ExplanationText text={data.text} source={data.source} />;
  }

  return (
    <section
      className={styles.panel}
      aria-labelledby={headingId}
      aria-live="polite"
      aria-busy={isPending || isPlaceholderData}
    >
      <h2 id={headingId} className={styles.heading}>
        한눈에 보기
      </h2>
      {body}
    </section>
  );
}

function ExplanationText({ text, source }: Pick<ExplanationResponse, "text" | "source">) {
  return (
    <>
      <p className={styles.text}>{text}</p>
      <p className={styles.source}>{EXPLANATION_SOURCE_TEXT[source]}</p>
    </>
  );
}
