import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import {
  ALTERNATIVE_STATUS_TEXT,
  ALTERNATIVES_STALE_TEXT,
  DEADLINE_TEXT,
  DESTINATION_ARRIVAL_UNAVAILABLE,
  NO_PROBABILITY_TEXT,
  SWITCH_SUGGESTION_TITLE,
  switchSuggestionText,
} from "../../../lib/labels";
import {
  cloneSnapshot,
  exampleSnapshot,
  snapshotWithAlternativeStatus,
} from "../../../test/fixtures";
import { AlternativesSection } from "./AlternativesSection";

describe("AlternativesSection", () => {
  it("recommended: 추천 표시와 바꿔 타기 제안을 강조한다", () => {
    render(<AlternativesSection snapshot={exampleSnapshot} />);

    expect(screen.getByText(SWITCH_SUGGESTION_TITLE)).toBeInTheDocument();
    expect(screen.getByText(switchSuggestionText("G1300", "1306"))).toBeInTheDocument();
    expect(screen.getByText(ALTERNATIVE_STATUS_TEXT.recommended.description)).toBeInTheDocument();

    const [g1300, bus1306] = screen.getAllByRole("listitem");
    expect(within(bus1306).getByText("추천")).toBeInTheDocument();
    expect(within(g1300).queryByText("추천")).toBeNull();

    // 목적지 도착·마감·0석 위험을 함께 보인다
    expect(within(g1300).getByText("08:21")).toBeInTheDocument();
    expect(within(g1300).getByText("위험 높음")).toBeInTheDocument();
    expect(within(g1300).getByText("24회 중 18회")).toBeInTheDocument();
    expect(within(g1300).getByText("예비")).toBeInTheDocument();
    expect(within(bus1306).getByText("위험 낮음")).toBeInTheDocument();
    expect(within(bus1306).getByText("25회 중 5회")).toBeInTheDocument();
  });

  it("no_alternative: 대안 없음 문구와 마감 넘음", () => {
    render(<AlternativesSection snapshot={snapshotWithAlternativeStatus("no_alternative")} />);
    expect(screen.getByText(ALTERNATIVE_STATUS_TEXT.no_alternative.title)).toBeInTheDocument();
    expect(
      screen.getByText(ALTERNATIVE_STATUS_TEXT.no_alternative.description),
    ).toBeInTheDocument();
    expect(screen.getAllByText(DEADLINE_TEXT.misses)).toHaveLength(2);
    expect(screen.queryByText(SWITCH_SUGGESTION_TITLE)).toBeNull();
  });

  it("arrival_unavailable: 도착시각 미제공 문구와 마감 판단 불가", () => {
    render(<AlternativesSection snapshot={snapshotWithAlternativeStatus("arrival_unavailable")} />);
    expect(
      screen.getByText(ALTERNATIVE_STATUS_TEXT.arrival_unavailable.description),
    ).toBeInTheDocument();
    // 상태 제목과 후보 2개의 목적지 도착 칸
    expect(screen.getAllByText(DESTINATION_ARRIVAL_UNAVAILABLE)).toHaveLength(3);
    expect(screen.getAllByText(DEADLINE_TEXT.unknown)).toHaveLength(2);
  });

  it("undecidable: 판단 불가 문구와 확률 없음", () => {
    render(<AlternativesSection snapshot={snapshotWithAlternativeStatus("undecidable")} />);
    expect(screen.getByText(ALTERNATIVE_STATUS_TEXT.undecidable.title)).toBeInTheDocument();
    expect(screen.getByText(ALTERNATIVE_STATUS_TEXT.undecidable.description)).toBeInTheDocument();
    expect(screen.getAllByText(NO_PROBABILITY_TEXT)).toHaveLength(2);
    expect(screen.queryByText("추천")).toBeNull();
  });

  it("스냅샷이 오래됐으면 추천·바꿔 타기 제안을 숨기고 정보 오래됨을 안내한다", () => {
    const snapshot = cloneSnapshot();
    snapshot.stale = true;
    render(<AlternativesSection snapshot={snapshot} />);

    expect(screen.getByText(ALTERNATIVES_STALE_TEXT.description)).toBeInTheDocument();
    expect(screen.queryByText(SWITCH_SUGGESTION_TITLE)).toBeNull();
    expect(screen.queryByText("추천")).toBeNull();
    expect(screen.queryByText(ALTERNATIVE_STATUS_TEXT.recommended.description)).toBeNull();
    expect(screen.queryByText("위험 높음")).toBeNull();
  });

  it("vehicleId 가 없는 후보(timetable_next)는 n·k 없이 예비만 붙인다", () => {
    const snapshot = cloneSnapshot();
    const timetableNext = snapshot.alternatives.candidates[1];
    timetableNext.source = "timetable_next";
    timetableNext.vehicleId = null;
    render(<AlternativesSection snapshot={snapshot} />);

    const item = screen.getAllByRole("listitem")[1];
    expect(within(item).getByText("위험 낮음")).toBeInTheDocument();
    expect(within(item).getByText("예비")).toBeInTheDocument();
    expect(within(item).queryByText(/회 중/)).toBeNull();
  });

  it("추천 후보를 찾지 못해도 바꿔 타기 문장이 어색하지 않다", () => {
    const snapshot = cloneSnapshot();
    snapshot.alternatives.recommended = null;
    render(<AlternativesSection snapshot={snapshot} />);

    const text = switchSuggestionText("G1300", null);
    expect(screen.getByText(text)).toBeInTheDocument();
    expect(text).not.toContain("(추천)");
  });
});
