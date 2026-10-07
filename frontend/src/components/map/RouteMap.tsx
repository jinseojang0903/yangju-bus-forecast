import { lazy, Suspense } from "react";
import styles from "./RouteMap.module.css";
import type { RouteMapProps } from "./types";

// 지도 라이브러리(Leaflet)는 예보 화면에서만 쓰이므로 따로 나눠 받는다.
// 다른 지도로 바꿀 때는 이 import 대상만 바꾸면 된다.
const LeafletRouteMap = lazy(() => import("./LeafletRouteMap"));

export type {
  LatLng,
  RouteMapProps,
  RouteMapStop,
  RouteMapVehicle,
  VehicleSeatTone,
} from "./types";

interface RouteMapComponentProps extends RouteMapProps {
  /** 지도 코드를 받는 동안 보일 문구 */
  loadingText: string;
}

/** 노선 지도. 화면은 이 컴포넌트만 쓰고 지도 라이브러리를 직접 import 하지 않는다. */
export function RouteMap({ loadingText, ...props }: RouteMapComponentProps) {
  return (
    <Suspense fallback={<p className={styles.placeholder}>{loadingText}</p>}>
      <LeafletRouteMap {...props} />
    </Suspense>
  );
}
