import { render, screen } from "@testing-library/react";
import { Map as LeafletMap } from "leaflet";
import { afterEach, beforeAll, describe, expect, it, vi } from "vitest";
import { RouteMap } from "./RouteMap";
import type { RouteMapProps } from "./types";

const props: RouteMapProps = {
  label: "G1300 노선 지도",
  outboundPath: [
    { lat: 37.79, lng: 127.06 },
    { lat: 37.77, lng: 127.08 },
  ],
  returnPath: [],
  stops: [
    { key: "1", position: { lat: 37.79, lng: 127.06 }, name: "<i>고읍중앙</i>", isTarget: false },
    { key: "2", position: { lat: 37.77, lng: 127.08 }, name: "덕현초교", isTarget: true },
  ],
  vehicles: [
    {
      key: "a",
      position: { lat: 37.79, lng: 127.06 },
      label: "0석",
      tone: "noSeat",
      description: "잔여 0석 · 고읍중앙 도착",
      isDimmed: false,
    },
    {
      key: "b",
      position: { lat: 37.78, lng: 127.07 },
      label: "<b>정보 없음</b>",
      tone: "unknown",
      description: "<img src=x>잔여석 정보 없음",
      isDimmed: false,
    },
  ],
  focus: [{ lat: 37.77, lng: 127.08 }],
  focusRequestId: 0,
};

const LOADING = "지도를 불러오는 중…";
const WAIT = { timeout: 5000 };

async function renderMap(overrides: Partial<RouteMapProps> = {}) {
  const view = render(<RouteMap {...props} {...overrides} loadingText={LOADING} />);
  await screen.findByRole("region", { name: "G1300 노선 지도" }, WAIT);
  return view;
}

// jsdom 에는 화면 크기가 없어 타일은 그려지지 않는다. 지도 코드가 깨지지 않고 글자가 들어가는지만 본다.
describe("RouteMap (Leaflet)", () => {
  // 지연 로딩되는 Leaflet 모듈을 미리 받아 둔다(전체 테스트를 병렬로 돌리면 첫 import 가 1초를 넘을 수 있다).
  beforeAll(async () => {
    await import("./LeafletRouteMap");
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("차량 잔여석 글자, 내 정류장 이름, OSM 저작권 표기를 그린다", async () => {
    await renderMap();

    expect(screen.getByText("0석")).toBeInTheDocument();
    expect(screen.getByText("덕현초교")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "OpenStreetMap" })).toHaveAttribute(
      "href",
      "https://www.openstreetmap.org/copyright",
    );
  });

  it("마커 글자와 툴팁은 HTML 로 해석하지 않는다", async () => {
    const { container } = await renderMap();
    expect(screen.getByText("<b>정보 없음</b>")).toBeInTheDocument();
    expect(container.querySelector(".leaflet-marker-pane b")).toBeNull();

    // 툴팁은 열릴 때 붙는다. 차량·정류장 툴팁을 열어 글자로만 들어갔는지 본다.
    const badge = screen.getByText("<b>정보 없음</b>").closest(".leaflet-marker-icon");
    badge?.dispatchEvent(new MouseEvent("mouseover", { bubbles: true }));
    expect(await screen.findByText("<img src=x>잔여석 정보 없음")).toBeInTheDocument();
    expect(container.querySelector(".leaflet-tooltip-pane img")).toBeNull();
    expect(container.querySelector(".leaflet-tooltip-pane i")).toBeNull();
  });

  it("휠 확대는 끈다(페이지 스크롤을 막지 않게)", async () => {
    const fitBounds = vi.spyOn(LeafletMap.prototype, "fitBounds");
    await renderMap();
    const map = fitBounds.mock.contexts.at(-1) as LeafletMap | undefined;
    expect(map?.scrollWheelZoom.enabled()).toBe(false);
    expect(map?.dragging.enabled()).toBe(true);
  });

  it("위치가 갱신돼도 화면을 다시 맞추지 않고, '다시 맞추기' 요청 때만 맞춘다", async () => {
    const fitBounds = vi.spyOn(LeafletMap.prototype, "fitBounds");
    const { rerender } = await renderMap();
    const initialCalls = fitBounds.mock.calls.length;
    expect(initialCalls).toBeGreaterThan(0);

    // 차량·focus 가 바뀌는 갱신
    const moved = [{ ...props.vehicles[0], key: "a", label: "1석", tone: "available" as const }];
    const newFocus = [{ lat: 37.79, lng: 127.06 }];
    rerender(<RouteMap {...props} vehicles={moved} focus={newFocus} loadingText={LOADING} />);
    expect(await screen.findByText("1석")).toBeInTheDocument();
    expect(screen.queryByText("0석")).toBeNull();
    expect(fitBounds).toHaveBeenCalledTimes(initialCalls);

    rerender(
      <RouteMap
        {...props}
        vehicles={moved}
        focus={newFocus}
        focusRequestId={1}
        loadingText={LOADING}
      />,
    );
    expect(fitBounds).toHaveBeenCalledTimes(initialCalls + 1);
    const [bounds] = fitBounds.mock.calls.at(-1) ?? [];
    expect(bounds && "getCenter" in bounds ? bounds.getCenter() : null).toMatchObject({
      lat: 37.79,
      lng: 127.06,
    });
  });

  it("화면에서 빠지면 지도를 정리한다", async () => {
    const remove = vi.spyOn(LeafletMap.prototype, "remove");
    const { unmount } = await renderMap();
    unmount();
    expect(remove).toHaveBeenCalled();
  });
});
