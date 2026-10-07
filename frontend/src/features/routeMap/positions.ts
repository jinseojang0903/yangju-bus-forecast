// 노선 위치 응답(계약 4.8)을 지도·목록 입력으로 바꾸는 순수 함수.
// 잔여석·정류장 수는 서버 값을 그대로 쓰고, 여기서는 고르고 늘어놓기만 한다.
import { isApiError } from "../../api/client";
import type {
  RouteId,
  RoutePositionsResponse,
  RoutePositionsStation,
  RoutePositionsVehicle,
  SnapshotResponse,
  StationRoutesResponse,
  VehicleState,
} from "../../api/types";
import type {
  LatLng,
  RouteMapProps,
  RouteMapStop,
  RouteMapVehicle,
  VehicleSeatTone,
} from "../../components/map/RouteMap";
import { formatSeats } from "../../lib/format";
import {
  MY_STATION_FALLBACK_NAME,
  remainSeatsText,
  stopsToTargetText,
  vehicleStateText,
} from "../../lib/labels";
import { errorRefreshDelayMs, snapshotRefreshDelayMs } from "../forecast/refresh";

/** 다가오는 차가 없어도 내 정류장 앞쪽이 조금 보이도록 처음 화면에 넣을 앞 정류장 수 */
export const FOCUS_STOPS_BEFORE_TARGET = 3;

export interface MapRoute {
  routeId: RouteId;
  routeName: string;
}

// ── 노선 고르기 ───────────────────────────────────────────────

/** 스냅샷에 나온 노선(버스 → 대안 후보 순, 처음 나온 순서). 같은 노선은 한 번만 */
export function routesFromSnapshot(snapshot: SnapshotResponse): MapRoute[] {
  const routes: MapRoute[] = [];
  const add = (routeId: RouteId, routeName: string) => {
    if (!routes.some((route) => route.routeId === routeId)) routes.push({ routeId, routeName });
  };
  for (const bus of snapshot.buses) add(bus.routeId, bus.routeName);
  for (const candidate of snapshot.alternatives.candidates) {
    add(candidate.routeId, candidate.routeName);
  }
  return routes;
}

/**
 * 지도에 보일 노선. 스냅샷에 노선이 있으면 그것을 쓰고,
 * 수집 시간 밖처럼 스냅샷에 버스가 없으면 정류장·목적지 기준정보(4.3)의 노선을 쓴다.
 */
export function routesForMap(
  snapshot: SnapshotResponse,
  stationRoutes: StationRoutesResponse | undefined,
): MapRoute[] {
  const fromSnapshot = routesFromSnapshot(snapshot);
  if (fromSnapshot.length > 0 || !stationRoutes) return fromSnapshot;
  const destination = stationRoutes.destinations.find(
    (item) => item.destinationId === snapshot.destination.destinationId,
  );
  return (destination?.routes ?? []).map(({ routeId, routeName }) => ({ routeId, routeName }));
}

// ── 다시 조회 ─────────────────────────────────────────────────

/**
 * 다음 자동 조회까지 기다릴 시간(ms). 스냅샷과 같은 규칙(서버 두 시각의 간격, 최소 10초·최대 30분,
 * 400·404 는 다시 부르지 않음)을 그대로 쓴다. false 면 자동 조회하지 않는다.
 */
export function positionsRefreshDelayMs(
  data: RoutePositionsResponse | undefined,
  error: unknown,
): number | false {
  if (error) return errorRefreshDelayMs(error);
  if (!data) return false;
  return snapshotRefreshDelayMs({ nextRefreshAt: data.nextRefreshAt, computedAt: data.computedAt });
}

/**
 * 다시 불러도 같은 실패(400 VALIDATION_FAILED·404 NOT_FOUND). 이 노선은 지도를 제공하지 않는 것으로 보고
 * 노선 버튼을 비활성으로 표시한다. 500·네트워크 오류는 잠깐의 실패라 해당하지 않는다.
 */
export function isPermanentPositionsError(error: unknown): boolean {
  return isApiError(error) && (error.code === "VALIDATION_FAILED" || error.code === "NOT_FOUND");
}

// ── 잔여석 ────────────────────────────────────────────────────

/** null(정보 없음)과 0(0석)은 서로 다른 구분이다(계약 4.8) */
export function seatTone(remainSeats: number | null): VehicleSeatTone {
  if (remainSeats === null) return "unknown";
  return remainSeats <= 0 ? "noSeat" : "available";
}

// ── 다가오는 차량 ─────────────────────────────────────────────

/**
 * stopsToTarget 이 같을 때의 순서. 서버는 정류장 순번만으로 남은 정류장 수를 세므로,
 * 같은 순번이라도 departed(그 정류장을 떠남)·passing(stateCd 0, 그 정류장 다음 정류장 사이 이동 중)인 차는
 * arrived(아직 그 정류장에 서 있음)인 차보다 내 정류장에 더 가깝다. unknown 은 위치를 가늠할 수 없어 맨 뒤에 둔다.
 */
const STATE_ORDER: Record<VehicleState, number> = {
  departed: 0,
  passing: 0,
  arrived: 1,
  unknown: 2,
};

export type ApproachingVehicle = RoutePositionsVehicle & { stopsToTarget: number };

/** 내 정류장으로 오는 차(stopsToTarget 이 있는 차)를 가까운 순으로 */
export function approachingVehicles(vehicles: RoutePositionsVehicle[]): ApproachingVehicle[] {
  return vehicles
    .filter((vehicle): vehicle is ApproachingVehicle => vehicle.stopsToTarget !== null)
    .sort(
      (a, b) =>
        a.stopsToTarget - b.stopsToTarget ||
        STATE_ORDER[a.state] - STATE_ORDER[b.state] ||
        a.vehicleId.localeCompare(b.vehicleId),
    );
}

// ── 지도 그리기 입력 ──────────────────────────────────────────

function isValidCoordinate(station: RoutePositionsStation): boolean {
  return (
    Number.isFinite(station.lat) &&
    Number.isFinite(station.lng) &&
    Math.abs(station.lat) <= 90 &&
    Math.abs(station.lng) <= 180
  );
}

function toLatLng(station: RoutePositionsStation): LatLng {
  return { lat: station.lat, lng: station.lng };
}

/** 지도 영역에 함께 둘 정보(내 정류장 이름 등) */
export function targetStation(response: RoutePositionsResponse): RoutePositionsStation | undefined {
  return (
    response.stations.find((station) => station.isTarget && station.isOutbound) ??
    response.stations.find((station) => station.isTarget)
  );
}

export function targetStationName(response: RoutePositionsResponse): string {
  return targetStation(response)?.name ?? MY_STATION_FALLBACK_NAME;
}

/**
 * 차량을 놓을 지점. GBIS 는 정류장 순번만 주므로
 * 도착·상태 미확인은 그 정류장에, 출발·통과는 다음 정류장과의 가운데에 둔다(표시용 근사).
 * 정류장 좌표를 찾지 못하면 null(지도에는 빼고 목록에는 남긴다).
 */
export function vehicleLatLng(
  vehicle: RoutePositionsVehicle,
  stations: RoutePositionsStation[],
): LatLng | null {
  const index = stations.findIndex((station) => station.stationSeq === vehicle.stationSeq);
  const station =
    index >= 0
      ? stations[index]
      : stations.find((candidate) => candidate.stationId === vehicle.stationId);
  if (!station || !isValidCoordinate(station)) return null;

  const isMoving = vehicle.state === "departed" || vehicle.state === "passing";
  const next = index >= 0 ? stations[index + 1] : undefined;
  if (isMoving && next && isValidCoordinate(next)) {
    return { lat: (station.lat + next.lat) / 2, lng: (station.lng + next.lng) / 2 };
  }
  return toLatLng(station);
}

function stationNameAt(stations: RoutePositionsStation[], vehicle: RoutePositionsVehicle) {
  return (
    stations.find((station) => station.stationSeq === vehicle.stationSeq)?.name ??
    stations.find((station) => station.stationId === vehicle.stationId)?.name ??
    null
  );
}

/** 지도 마커를 눌렀을 때 보일 설명. 예: "잔여 0석 · 고읍지구 출발 · 덕현초교까지 2정류장 · 경기76바8260" */
export function vehicleDescription(
  vehicle: RoutePositionsVehicle,
  stations: RoutePositionsStation[],
  targetName: string,
): string {
  const parts = [
    remainSeatsText(vehicle.remainSeats),
    vehicleStateText(vehicle.state, stationNameAt(stations, vehicle)),
  ];
  if (vehicle.stopsToTarget !== null) {
    parts.push(stopsToTargetText(targetName, vehicle.stopsToTarget));
  }
  if (vehicle.plateNo) parts.push(vehicle.plateNo);
  return parts.join(" · ");
}

export type RouteMapView = Omit<RouteMapProps, "label" | "focusRequestId">;

/**
 * 지도 컴포넌트 입력. 잠실행 구간은 진한 선과 정류장 점, 귀로는 흐린 선만 그린다.
 * focus 는 내 정류장 + 그 앞 몇 정류장 + 다가오는 차량이다.
 */
export function toRouteMapView(response: RoutePositionsResponse): RouteMapView {
  const stations = response.stations.filter(isValidCoordinate);
  const outbound = stations.filter((station) => station.isOutbound);
  const returning = stations.filter((station) => !station.isOutbound);

  // 귀로 선은 회차 지점(잠실행 마지막 정류장)에서 이어지게 한다
  const lastOutbound = outbound.at(-1);
  const returnStations = returning.length > 0 && lastOutbound ? [lastOutbound, ...returning] : [];

  const stops: RouteMapStop[] = outbound.map((station) => ({
    key: `${station.stationSeq}-${station.stationId}`,
    position: toLatLng(station),
    name: station.name,
    isTarget: station.isTarget,
  }));

  const targetName = targetStationName(response);
  const vehicles: RouteMapVehicle[] = [];
  for (const vehicle of response.vehicles) {
    const position = vehicleLatLng(vehicle, response.stations);
    if (!position) continue;
    vehicles.push({
      key: vehicle.vehicleId,
      position,
      label: formatSeats(vehicle.remainSeats),
      tone: seatTone(vehicle.remainSeats),
      description: vehicleDescription(vehicle, response.stations, targetName),
      isDimmed: vehicle.stopsToTarget === null,
    });
  }

  return {
    outboundPath: outbound.map(toLatLng),
    returnPath: returnStations.map(toLatLng),
    stops,
    vehicles,
    focus: focusPoints(response, stations),
  };
}

function focusPoints(
  response: RoutePositionsResponse,
  stations: RoutePositionsStation[],
): LatLng[] {
  const target = targetStation(response);
  if (!target || !isValidCoordinate(target)) {
    const outbound = stations.filter((station) => station.isOutbound);
    return (outbound.length > 0 ? outbound : stations).map(toLatLng);
  }
  const before = stations.filter(
    (station) =>
      station.isOutbound &&
      station.stationSeq < target.stationSeq &&
      station.stationSeq >= target.stationSeq - FOCUS_STOPS_BEFORE_TARGET,
  );
  const approaching = approachingVehicles(response.vehicles)
    .map((vehicle) => vehicleLatLng(vehicle, response.stations))
    .filter((position): position is LatLng => position !== null);
  return [toLatLng(target), ...before.map(toLatLng), ...approaching];
}
