import "leaflet/dist/leaflet.css";
import {
  circleMarker,
  map as createMap,
  divIcon,
  type LayerGroup,
  type Map as LeafletMap,
  latLngBounds,
  layerGroup,
  marker,
  polyline,
  tileLayer,
} from "leaflet";
import { useEffect, useRef } from "react";
import { cx } from "../../lib/classNames";
import styles from "./RouteMap.module.css";
import type {
  LatLng,
  RouteMapProps,
  RouteMapStop,
  RouteMapVehicle,
  VehicleSeatTone,
} from "./types";

// react-leaflet(Hippocratic 라이선스)은 쓰지 않고 leaflet(BSD-2)만 직접 다룬다.

// OpenStreetMap 타일(키 없음). 저작권 표기는 OSM 이용 조건상 반드시 보여야 한다.
// 이 문자열은 Leaflet 이 HTML 로 넣으므로 고정 상수만 쓴다(서버·사용자 문자열 금지).
// TODO(jsjang/2026-10-07): OSM 공용 타일은 시연용이다. 배포 전 다른 타일로 교체하거나 백엔드 프록시를 검토한다(팀 결정 대기).
const TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png";
const TILE_ATTRIBUTION =
  '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';
const TILE_MAX_ZOOM = 19;

/** 점 하나만 맞출 때 너무 확대되지 않게 */
const FIT_MAX_ZOOM = 15;
const FIT_OPTIONS = { padding: [28, 28] as [number, number], maxZoom: FIT_MAX_ZOOM };

const STOP_RADIUS = 3;
const TARGET_STOP_RADIUS = 9;
/** 내 정류장으로 오는 차를 흐린 차 위에 그린다 */
const ACTIVE_VEHICLE_Z_OFFSET = 1000;

const TONE_CLASS: Record<VehicleSeatTone, string | undefined> = {
  available: styles.vehicleAvailable,
  noSeat: styles.vehicleNoSeat,
  unknown: styles.vehicleUnknown,
};

function toTuple(point: LatLng): [number, number] {
  return [point.lat, point.lng];
}

function fitTo(map: LeafletMap, points: LatLng[]) {
  if (points.length > 0) map.fitBounds(latLngBounds(points.map(toTuple)), FIT_OPTIONS);
}

/**
 * Leaflet 툴팁·아이콘에 문자열을 주면 innerHTML 로 들어간다.
 * 서버 문자열(정류장 이름·차량 설명)은 요소를 만들어 textContent 로만 넣는다.
 */
function textElement(text: string, className?: string): HTMLSpanElement {
  const element = document.createElement("span");
  if (className) element.className = className;
  element.textContent = text;
  return element;
}

function drawStop(group: LayerGroup, stop: RouteMapStop) {
  const layer = circleMarker(toTuple(stop.position), {
    radius: stop.isTarget ? TARGET_STOP_RADIUS : STOP_RADIUS,
    className: stop.isTarget ? styles.targetStop : styles.stop,
  });
  if (stop.isTarget) {
    // 내 정류장 이름은 늘 보인다
    layer.bindTooltip(textElement(stop.name), {
      permanent: true,
      direction: "top",
      offset: [0, -8],
      className: styles.targetLabel,
    });
  } else {
    layer.bindTooltip(textElement(stop.name));
  }
  group.addLayer(layer);
}

function drawVehicle(group: LayerGroup, vehicle: RouteMapVehicle) {
  const badge = textElement(
    vehicle.label,
    cx(styles.vehicle, TONE_CLASS[vehicle.tone], vehicle.isDimmed && styles.vehicleDimmed),
  );
  // 크기 0 의 기준점에 붙이고 글자 상자는 CSS 로 가운데 맞춘다(글자 길이에 따라 폭이 달라서)
  const icon = divIcon({ html: badge, className: styles.vehicleIcon, iconSize: [0, 0] });
  const layer = marker(toTuple(vehicle.position), {
    icon,
    // title·alt 는 Leaflet 이 속성 값으로 넣는다(HTML 해석 없음)
    title: vehicle.description,
    alt: vehicle.description,
    zIndexOffset: vehicle.isDimmed ? 0 : ACTIVE_VEHICLE_Z_OFFSET,
  });
  layer.bindTooltip(textElement(vehicle.description), { direction: "top", offset: [0, -12] });
  group.addLayer(layer);
}

/**
 * Leaflet + OpenStreetMap 으로 그린 노선 지도. RouteMap.tsx 가 지연 로딩한다.
 * 처음 그릴 때와 focusRequestId 가 바뀔 때만 focus 로 맞추고, 위치 갱신 때는 이용자가 옮긴 화면을 그대로 둔다.
 */
export default function LeafletRouteMap({
  label,
  outboundPath,
  returnPath,
  stops,
  vehicles,
  focus,
  focusRequestId,
}: RouteMapProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const mapRef = useRef<LeafletMap | null>(null);
  const overlayRef = useRef<LayerGroup | null>(null);
  // 지도 생성과 '다시 맞추기'는 그 시점의 최신 focus 를 쓰되, focus 가 바뀌었다고 화면을 옮기지는 않는다
  const focusRef = useRef(focus);
  const lastFocusRequestId = useRef(focusRequestId);

  // 지도 만들기·정리(한 번)
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const map = createMap(container, { scrollWheelZoom: false });
    tileLayer(TILE_URL, { attribution: TILE_ATTRIBUTION, maxZoom: TILE_MAX_ZOOM }).addTo(map);
    const overlay = layerGroup().addTo(map);
    fitTo(map, focusRef.current);
    mapRef.current = map;
    overlayRef.current = overlay;
    return () => {
      map.remove();
      mapRef.current = null;
      overlayRef.current = null;
    };
  }, []);

  // 위치가 갱신되면 선·점·차량만 다시 그린다(화면 위치·확대는 건드리지 않음)
  useEffect(() => {
    const overlay = overlayRef.current;
    if (!overlay) return;
    overlay.clearLayers();
    if (returnPath.length > 1) {
      overlay.addLayer(polyline(returnPath.map(toTuple), { className: styles.returnLine }));
    }
    if (outboundPath.length > 1) {
      overlay.addLayer(polyline(outboundPath.map(toTuple), { className: styles.outboundLine }));
    }
    // 내 정류장 점을 다른 정류장 위에 그린다
    for (const stop of stops) if (!stop.isTarget) drawStop(overlay, stop);
    for (const stop of stops) if (stop.isTarget) drawStop(overlay, stop);
    for (const vehicle of vehicles) drawVehicle(overlay, vehicle);
  }, [outboundPath, returnPath, stops, vehicles]);

  useEffect(() => {
    focusRef.current = focus;
  }, [focus]);

  // '내 정류장 중심으로'
  useEffect(() => {
    if (lastFocusRequestId.current === focusRequestId) return;
    lastFocusRequestId.current = focusRequestId;
    if (mapRef.current) fitTo(mapRef.current, focusRef.current);
  }, [focusRequestId]);

  return (
    <section className={styles.frame} aria-label={label}>
      <div ref={containerRef} className={styles.map} />
    </section>
  );
}
