import type { RiskLevel } from "../api/types";
import { cx } from "../lib/classNames";
import { RISK_LABEL, RISK_SYMBOL } from "../lib/labels";
import styles from "./RiskBadge.module.css";

/** 위험 등급. 색 + 모양 기호 + 문구를 함께 써서 색만으로 구분하지 않는다. */
export function RiskBadge({ level }: { level: RiskLevel }) {
  return (
    <span className={cx(styles.badge, styles[level])}>
      <span aria-hidden="true" className={styles.symbol}>
        {RISK_SYMBOL[level]}
      </span>
      <span>{RISK_LABEL[level]}</span>
    </span>
  );
}
