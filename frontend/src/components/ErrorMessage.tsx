import { Link } from "react-router";
import { isApiError } from "../api/client";
import { cx } from "../lib/classNames";
import styles from "./ErrorMessage.module.css";

// 에러 문구는 이 파일 한 곳에서 정한다(.claude/rules/frontend.md).
// 서버 message·details·예외 내용은 화면에 보여 주지 않는다.

export type ErrorContext = "default" | "parseQuery";

export interface ErrorText {
  title: string;
  detail: string | null;
  /** retry: 다시 시도 버튼, reselect: 조건 입력으로 돌아가기, none: 안내만 */
  action: "retry" | "reselect" | "none";
}

const TEMPORARY_FAILURE: ErrorText = {
  title: "일시적인 문제로 정보를 불러오지 못했어요.",
  detail: "잠시 뒤 다시 시도해 주세요.",
  action: "retry",
};

export function describeError(error: unknown, context: ErrorContext = "default"): ErrorText {
  if (!isApiError(error)) return TEMPORARY_FAILURE;
  switch (error.code) {
    case "VALIDATION_FAILED":
      if (context === "parseQuery") {
        return { title: "질문은 1–200자로 적어 주세요.", detail: null, action: "none" };
      }
      return {
        title: "조회 조건이 올바르지 않아요.",
        detail: "정류장·목적지·도착 마감을 다시 골라 주세요.",
        action: "reselect",
      };
    case "NOT_FOUND":
      return {
        title: "정류장이나 목적지를 찾을 수 없어요.",
        detail: "조건을 다시 골라 주세요.",
        action: "reselect",
      };
    case "RATE_LIMITED":
      return {
        title: "요청이 너무 많아요.",
        detail:
          error.retryAfterSec !== null
            ? `${error.retryAfterSec}초 뒤에 다시 시도해 주세요.`
            : "잠시 뒤에 다시 시도해 주세요.",
        action: "retry",
      };
    case "NETWORK_ERROR":
      return {
        title: "인터넷 연결을 확인해 주세요.",
        detail: "연결되면 다시 시도해 주세요.",
        action: "retry",
      };
    case "INVALID_RESPONSE":
      return {
        title: "받은 정보를 읽지 못했어요.",
        detail: "잠시 뒤 다시 시도해 주세요.",
        action: "retry",
      };
    case "METHOD_NOT_ALLOWED":
    case "INTERNAL_ERROR":
      return TEMPORARY_FAILURE;
  }
}

interface ErrorMessageProps {
  error: unknown;
  onRetry?: () => void;
  context?: ErrorContext;
  /** 이전 숫자를 유지한 채 위에 띄우는 작은 형태(갱신 실패) */
  compact?: boolean;
}

export function ErrorMessage({
  error,
  onRetry,
  context = "default",
  compact = false,
}: ErrorMessageProps) {
  const text = describeError(error, context);
  return (
    <div className={cx(styles.box, compact && styles.compact)} aria-live="assertive">
      <p className={styles.title}>{text.title}</p>
      {text.detail && <p className={styles.detail}>{text.detail}</p>}
      {text.action === "retry" && onRetry && (
        <button type="button" className={styles.button} onClick={onRetry}>
          다시 시도
        </button>
      )}
      {text.action === "reselect" && (
        <Link to="/" className={styles.link}>
          조건 다시 고르기
        </Link>
      )}
    </div>
  );
}
