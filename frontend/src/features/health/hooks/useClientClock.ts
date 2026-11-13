import { useEffect, useState } from "react";

/**
 * 기기 시각(ms)을 tickMs 마다 갱신해 다시 그리게 한다. 경과 시간 표시에만 쓴다.
 * resetKey 가 바뀌면(새 응답을 받은 때) 바로 Date.now() 로 다시 맞추고 틱을 그 시점부터 다시 센다.
 * 그러지 않으면 새 응답 직후에도 최대 tickMs 만큼 낡은 시각으로 그린다.
 * 언마운트하면 타이머를 정리한다.
 */
export function useClientClock(tickMs: number, resetKey?: unknown): number {
  const [nowMs, setNowMs] = useState(() => Date.now());
  // biome-ignore lint/correctness/useExhaustiveDependencies: resetKey 는 값을 읽지 않고, 바뀌었을 때 시계를 다시 맞추는 신호로만 쓴다
  useEffect(() => {
    setNowMs(Date.now());
    const timer = window.setInterval(() => setNowMs(Date.now()), tickMs);
    return () => window.clearInterval(timer);
  }, [tickMs, resetKey]);
  return nowMs;
}
