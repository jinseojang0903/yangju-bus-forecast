import { describe, expect, it } from "vitest";
import { resolveChoice, toTimeOfDay } from "./selection";

describe("resolveChoice", () => {
  it("목록에 있는 선택은 그대로, 없으면 첫 값", () => {
    expect(resolveChoice("b", ["a", "b"])).toBe("b");
    expect(resolveChoice("z", ["a", "b"])).toBe("a");
    expect(resolveChoice(null, ["a"])).toBe("a");
    expect(resolveChoice(null, [])).toBeNull();
  });
});

describe("toTimeOfDay", () => {
  it("HH:MM 으로 맞춘다", () => {
    expect(toTimeOfDay("08:30")).toBe("08:30");
    expect(toTimeOfDay("08:30:00")).toBe("08:30");
  });

  it("비었거나 형식이 아니면 마감 없음", () => {
    expect(toTimeOfDay("")).toBeUndefined();
    expect(toTimeOfDay("8:30")).toBeUndefined();
    expect(toTimeOfDay("24:00")).toBeUndefined();
  });
});
