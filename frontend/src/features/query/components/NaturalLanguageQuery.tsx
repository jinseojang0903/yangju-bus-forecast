import { useId, useState } from "react";
import type { ParseQueryResponse } from "../../../api/types";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { Notice } from "../../../components/Notice";
import { PARSE_QUERY_TEXT } from "../../../lib/labels";
import { useParseQuery } from "../hooks/useParseQuery";
import styles from "./QueryPage.module.css";

/** 계약 4.6: 1–200자 */
const MAX_QUESTION_LENGTH = 200;

interface NaturalLanguageQueryProps {
  /** status 가 parsed 일 때 찾은 조건을 넘긴다. 실제 조회는 '예보 보기' 로 한다 */
  onParsed: (query: ParseQueryResponse["query"]) => void;
}

/** 말로 묻기. 뼈대 단계 서버는 항상 unavailable 을 준다. */
export function NaturalLanguageQuery({ onParsed }: NaturalLanguageQueryProps) {
  const titleId = useId();
  const inputId = useId();
  const hintId = useId();
  const [question, setQuestion] = useState("");
  const [isEmptyQuestion, setIsEmptyQuestion] = useState(false);
  const parseQuery = useParseQuery();

  const submitQuestion = () => {
    const trimmed = question.trim();
    if (!trimmed) {
      setIsEmptyQuestion(true);
      return;
    }
    setIsEmptyQuestion(false);
    parseQuery.mutate(trimmed, {
      onSuccess: (result) => {
        if (result.status === "parsed") onParsed(result.query);
      },
    });
  };

  return (
    <section className={styles.card} aria-labelledby={titleId}>
      <h2 id={titleId} className={styles.subtitle}>
        말로 물어보기
      </h2>
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          submitQuestion();
        }}
      >
        <div className={styles.field}>
          <label htmlFor={inputId} className={styles.label}>
            질문
          </label>
          <input
            id={inputId}
            className={styles.control}
            type="text"
            value={question}
            maxLength={MAX_QUESTION_LENGTH}
            placeholder="예: 8시 반까지 잠실 가야 해요"
            aria-describedby={hintId}
            onChange={(event) => setQuestion(event.target.value)}
          />
          <p id={hintId} className={styles.hint}>
            최대 {MAX_QUESTION_LENGTH}자. 이름·연락처·위치 같은 개인정보는 적지 마세요.
          </p>
        </div>
        <button type="submit" className={styles.secondaryButton} disabled={parseQuery.isPending}>
          {parseQuery.isPending ? "확인하는 중…" : "조건 찾기"}
        </button>
      </form>

      <div className={styles.result} aria-live="polite">
        {isEmptyQuestion && <p className={styles.hint}>질문을 적어 주세요.</p>}
        {parseQuery.error && (
          <ErrorMessage error={parseQuery.error} context="parseQuery" onRetry={submitQuestion} />
        )}
        {parseQuery.data && <ParseResult result={parseQuery.data} />}
      </div>
    </section>
  );
}

function ParseResult({ result }: { result: ParseQueryResponse }) {
  return (
    <Notice
      tone={result.status === "parsed" ? "info" : "warning"}
      title={PARSE_QUERY_TEXT[result.status]}
    >
      {/* 서버 문자열은 텍스트로만 넣는다 */}
      {result.status === "need_more" && result.message && <p>{result.message}</p>}
    </Notice>
  );
}
