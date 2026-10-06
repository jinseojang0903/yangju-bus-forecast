import { useMutation } from "@tanstack/react-query";
import { postParseQuery } from "../../../api/endpoints";

/** 자연어 질문 → 조회 조건(계약 4.6). 호출 제한이 빡빡해(분당 5회) 재시도하지 않는다. */
export function useParseQuery() {
  return useMutation({
    mutationFn: (text: string) => postParseQuery(text),
  });
}
