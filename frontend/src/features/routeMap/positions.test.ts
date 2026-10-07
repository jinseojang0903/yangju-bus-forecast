import { describe, expect, it } from "vitest";
import { ApiError, type ErrorCode } from "../../api/client";
import type { RoutePositionsVehicle } from "../../api/types";
import { formatSeats } from "../../lib/format";
import { remainSeatsText, stopsToTargetText, vehicleStateText } from "../../lib/labels";
import {
  clonePositions,
  examplePositions,
  exampleSnapshot,
  exampleStationRoutes,
  outsideCollectionSnapshot,
} from "../../test/fixtures";
import {
  approachingVehicles,
  isPermanentPositionsError,
  positionsRefreshDelayMs,
  routesForMap,
  routesFromSnapshot,
  seatTone,
  targetStationName,
  toRouteMapView,
  vehicleDescription,
  vehicleLatLng,
} from "./positions";

function vehicle(overrides: Partial<RoutePositionsVehicle>): RoutePositionsVehicle {
  return {
    vehicleId: "1",
    plateNo: null,
    stationSeq: 1,
    stationId: "235000001",
    state: "arrived",
    remainSeats: 3,
    stopsToTarget: null,
    ...overrides,
  };
}

describe("잔여석 표시", () => {
  it("null 은 '정보 없음', 0 은 '0석', 양수는 'n석'이고 구분이 서로 다르다", () => {
    expect(formatSeats(null)).toBe("정보 없음");
    expect(formatSeats(0)).toBe("0석");
    expect(formatSeats(3)).toBe("3석");

    expect(seatTone(null)).toBe("unknown");
    expect(seatTone(0)).toBe("noSeat");
    expect(seatTone(3)).toBe("available");
    expect(seatTone(null)).not.toBe(seatTone(0));
  });

  it("목록 문구도 null 과 0 을 다르게 쓴다", () => {
    expect(remainSeatsText(null)).toBe("잔여석 정보 없음");
    expect(remainSeatsText(0)).toBe("잔여 0석");
    expect(remainSeatsText(12)).toBe("잔여 12석");
  });

  it("남은 정류장 문구", () => {
    expect(stopsToTargetText("덕현초교", 3)).toBe("덕현초교까지 3정류장");
    expect(stopsToTargetText("덕현초교", 0)).toBe("덕현초교 도착·통과 중");
  });
});

describe("approachingVehicles", () => {
  it("stopsToTarget 이 있는 차만 가까운 순으로", () => {
    const ids = approachingVehicles(examplePositions.vehicles).map((item) => item.vehicleId);
    expect(ids).toEqual(["235000903", "235000902", "235000901"]);
  });

  it("남은 정류장이 같으면 이미 출발한 차가 먼저, 그다음 차량 ID 순", () => {
    const sorted = approachingVehicles([
      vehicle({ vehicleId: "b", state: "arrived", stopsToTarget: 2 }),
      vehicle({ vehicleId: "c", state: "departed", stopsToTarget: 2 }),
      vehicle({ vehicleId: "a", state: "arrived", stopsToTarget: 2 }),
      vehicle({ vehicleId: "z", state: "unknown", stopsToTarget: 1 }),
    ]);
    expect(sorted.map((item) => item.vehicleId)).toEqual(["z", "c", "a", "b"]);
  });

  it("다가오는 차가 없으면 빈 목록", () => {
    expect(approachingVehicles([vehicle({ stopsToTarget: null })])).toEqual([]);
  });
});

describe("vehicleLatLng", () => {
  const { stations } = examplePositions;

  it("도착은 그 정류장에", () => {
    expect(vehicleLatLng(vehicle({ stationSeq: 4, state: "arrived" }), stations)).toEqual({
      lat: 37.78,
      lng: 127.07,
    });
  });

  it("출발·통과는 다음 정류장과의 가운데에", () => {
    const position = vehicleLatLng(vehicle({ stationSeq: 3, state: "departed" }), stations);
    expect(position?.lat).toBeCloseTo(37.785);
    expect(position?.lng).toBeCloseTo(127.065);
  });

  it("마지막 정류장에서 출발했으면 그 정류장에", () => {
    expect(vehicleLatLng(vehicle({ stationSeq: 10, state: "departed" }), stations)).toEqual({
      lat: 37.771,
      lng: 127.081,
    });
  });

  it("순번이 없으면 정류장 ID 로 찾고, 둘 다 없으면 null", () => {
    expect(vehicleLatLng(vehicle({ stationSeq: 99, stationId: "235000392" }), stations)).toEqual({
      lat: 37.77,
      lng: 127.08,
    });
    expect(vehicleLatLng(vehicle({ stationSeq: 99, stationId: "999" }), stations)).toBeNull();
  });
});

describe("toRouteMapView", () => {
  it("잠실행은 진한 선과 정류장 점, 귀로는 회차 지점부터 흐린 선", () => {
    const view = toRouteMapView(examplePositions);
    expect(view.outboundPath).toHaveLength(8);
    expect(view.stops).toHaveLength(8);
    expect(view.stops.filter((stop) => stop.isTarget).map((stop) => stop.name)).toEqual([
      "덕현초교",
    ]);
    // 회차(8번) + 귀로 9·10번
    expect(view.returnPath).toEqual([
      { lat: 37.51, lng: 127.1 },
      { lat: 37.512, lng: 127.102 },
      { lat: 37.771, lng: 127.081 },
    ]);
  });

  it("차량 마커는 잔여석 글자와 구분을 함께 갖고, 다가오지 않는 차는 흐리게", () => {
    const view = toRouteMapView(examplePositions);
    const byKey = Object.fromEntries(view.vehicles.map((item) => [item.key, item]));
    expect(byKey["235000901"]).toMatchObject({ label: "0석", tone: "noSeat", isDimmed: false });
    expect(byKey["235000903"]).toMatchObject({
      label: "정보 없음",
      tone: "unknown",
      isDimmed: false,
    });
    expect(byKey["235000904"]).toMatchObject({ label: "10석", isDimmed: true });
    expect(byKey["235000905"]?.isDimmed).toBe(true);
  });

  it("처음 화면은 내 정류장 + 앞 3정류장 + 다가오는 차량", () => {
    const view = toRouteMapView(examplePositions);
    // 내 정류장(5) + 앞 정류장(2,3,4) + 다가오는 차량 3대
    expect(view.focus).toHaveLength(7);
    expect(view.focus[0]).toEqual({ lat: 37.77, lng: 127.08 });
    expect(view.focus).not.toContainEqual({ lat: 37.51, lng: 127.1 });
  });

  it("내 정류장이 없으면 잠실행 전체를 맞춘다", () => {
    const positions = clonePositions();
    for (const station of positions.stations) station.isTarget = false;
    expect(toRouteMapView(positions).focus).toHaveLength(8);
    expect(targetStationName(positions)).toBe("내 정류장");
  });

  it("좌표가 이상한 정류장은 빼고 그린다(지도 오류로 화면이 깨지지 않게)", () => {
    const positions = clonePositions();
    positions.stations[1].lat = Number.NaN;
    positions.stations[2].lng = 500;
    const view = toRouteMapView(positions);
    expect(view.outboundPath).toHaveLength(6);
    for (const point of [...view.outboundPath, ...view.focus]) {
      expect(Number.isFinite(point.lat)).toBe(true);
    }
  });

  it("설명에 잔여석·상태·남은 정류장·번호판을 짧게 담는다", () => {
    const [first] = examplePositions.vehicles;
    expect(vehicleDescription(first, examplePositions.stations, "덕현초교")).toBe(
      "잔여 0석 · 기점 도착 · 덕현초교까지 4정류장 · 경기76바0001",
    );
  });
});

describe("routesForMap", () => {
  it("스냅샷의 버스·대안 후보 노선을 처음 나온 순서로 한 번씩", () => {
    expect(routesFromSnapshot(exampleSnapshot)).toEqual([
      { routeId: "235000092", routeName: "G1300" },
      { routeId: "235000123", routeName: "1306" },
    ]);
  });

  it("스냅샷에 노선이 없으면 정류장 기준정보의 같은 목적지 노선을 쓴다", () => {
    const snapshot = outsideCollectionSnapshot();
    expect(routesForMap(snapshot, undefined)).toEqual([]);
    expect(routesForMap(snapshot, exampleStationRoutes).map((route) => route.routeName)).toEqual([
      "G1300",
      "1306",
    ]);
  });
});

describe("isPermanentPositionsError", () => {
  it("400·404 만 다시 불러도 같은 실패로 본다", () => {
    const error = (code: ErrorCode, status: number) => new ApiError({ code, status, message: "" });
    expect(isPermanentPositionsError(error("NOT_FOUND", 404))).toBe(true);
    expect(isPermanentPositionsError(error("VALIDATION_FAILED", 400))).toBe(true);
    expect(isPermanentPositionsError(error("INTERNAL_ERROR", 500))).toBe(false);
    expect(isPermanentPositionsError(error("RATE_LIMITED", 429))).toBe(false);
    expect(isPermanentPositionsError(new TypeError("Failed to fetch"))).toBe(false);
    expect(isPermanentPositionsError(null)).toBe(false);
  });
});

describe("vehicleStateText", () => {
  it("passing(stateCd 0)은 정류장 사이 이동 중", () => {
    expect(vehicleStateText("passing", "고읍지구")).toBe("정류장 사이 이동 중");
    expect(vehicleStateText("departed", "고읍지구")).toBe("고읍지구 출발");
    expect(vehicleStateText("arrived", null)).toBe("정류장 도착");
  });
});

describe("positionsRefreshDelayMs", () => {
  it("nextRefreshAt - computedAt 만큼, 최소 10초·최대 30분", () => {
    expect(positionsRefreshDelayMs(examplePositions, null)).toBe(13_000);

    const tooSoon = clonePositions();
    tooSoon.nextRefreshAt = "2026-10-07T07:31:22+09:00";
    expect(positionsRefreshDelayMs(tooSoon, null)).toBe(10_000);

    const outside = clonePositions();
    outside.inCollectionWindow = false;
    outside.nextRefreshAt = "2026-10-08T05:30:00+09:00";
    expect(positionsRefreshDelayMs(outside, null)).toBe(30 * 60_000);
  });

  it("404·400 은 다시 부르지 않고, 응답 전에는 기다리지 않는다", () => {
    const notFound = new ApiError({ code: "NOT_FOUND", status: 404, message: "" });
    expect(positionsRefreshDelayMs(undefined, notFound)).toBe(false);
    expect(positionsRefreshDelayMs(examplePositions, notFound)).toBe(false);
    expect(positionsRefreshDelayMs(undefined, null)).toBe(false);
  });
});
