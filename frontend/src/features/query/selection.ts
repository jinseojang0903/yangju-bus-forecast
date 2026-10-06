/** 고른 값이 목록에 있으면 그 값, 없으면 목록의 첫 값(목록이 비면 null) */
export function resolveChoice<T extends string>(choice: T | null, options: readonly T[]): T | null {
  if (choice !== null && options.includes(choice)) return choice;
  return options[0] ?? null;
}

const TIME_OF_DAY_PATTERN = /^([01]\d|2[0-3]):[0-5]\d/;

/**
 * <input type="time"> 값을 계약의 TimeOfDay("HH:MM")로 맞춘다.
 * 브라우저에 따라 "HH:MM:SS" 가 올 수 있어 앞 5자만 쓴다. 비었거나 형식이 아니면 undefined(마감 없음).
 */
export function toTimeOfDay(value: string): string | undefined {
  const trimmed = value.trim();
  return TIME_OF_DAY_PATTERN.test(trimmed) ? trimmed.slice(0, 5) : undefined;
}
