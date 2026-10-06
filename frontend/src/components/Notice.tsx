import type { ReactNode } from "react";
import { cx } from "../lib/classNames";
import styles from "./Notice.module.css";

type NoticeTone = "info" | "warning" | "highlight";

const TONE_SYMBOL: Record<NoticeTone, string> = {
  info: "i",
  warning: "!",
  highlight: "⇄",
};

interface NoticeProps {
  tone: NoticeTone;
  title: string;
  children?: ReactNode;
}

/** 상태 안내 상자. 색 외에 기호·굵은 제목·테두리 굵기로도 구분한다. */
export function Notice({ tone, title, children }: NoticeProps) {
  return (
    <div className={cx(styles.notice, styles[tone])}>
      <p className={styles.heading}>
        <span aria-hidden="true" className={styles.symbol}>
          {TONE_SYMBOL[tone]}
        </span>
        <strong>{title}</strong>
      </p>
      {children && <div className={styles.body}>{children}</div>}
    </div>
  );
}
