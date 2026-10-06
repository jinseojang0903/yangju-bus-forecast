import { PRELIMINARY_HINT, PRELIMINARY_LABEL } from "../lib/labels";
import styles from "./PreliminaryBadge.module.css";

/** 공개 기준 판정 전 확률에 반드시 붙이는 '예비' 표기 */
export function PreliminaryBadge() {
  return (
    <span className={styles.badge} title={PRELIMINARY_HINT}>
      {PRELIMINARY_LABEL}
    </span>
  );
}
