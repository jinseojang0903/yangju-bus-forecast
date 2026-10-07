import { useId, useMemo, useState } from "react";
import type { RoutePositionsResponse, SnapshotResponse } from "../../../api/types";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { RouteMap } from "../../../components/map/RouteMap";
import { Notice } from "../../../components/Notice";
import { cx } from "../../../lib/classNames";
import {
  APPROACHING_EMPTY,
  APPROACHING_HEADING,
  collectedAtText,
  ROUTE_MAP_HEADING,
  ROUTE_MAP_LOADING,
  ROUTE_MAP_NO_RECORD,
  ROUTE_MAP_NO_ROUTES,
  ROUTE_MAP_NO_STATIONS,
  ROUTE_MAP_OUTSIDE_COLLECTION_TEXT,
  ROUTE_MAP_RECENTER,
  ROUTE_MAP_REFRESHING,
  ROUTE_MAP_ROUTE_PICKER_LABEL,
  ROUTE_MAP_ROUTE_UNAVAILABLE,
  ROUTE_MAP_STALE_TEXT,
  remainSeatsText,
  routeMapAriaLabel,
  stopsToTargetText,
} from "../../../lib/labels";
import { formatKstTime } from "../../../lib/time";
import { useStationRoutes } from "../../query/hooks/useStationRoutes";
import { useRoutePositions } from "../hooks/useRoutePositions";
import {
  approachingVehicles,
  isPermanentPositionsError,
  type MapRoute,
  routesForMap,
  routesFromSnapshot,
  seatTone,
  targetStationName,
  toRouteMapView,
} from "../positions";
import styles from "./RouteMapSection.module.css";

/**
 * 예보 화면의 노선 지도(계약 4.8, 8장 '예보(지도)'). 스냅샷에 나온 노선(수집 시간 밖이면 4.3 기준정보 노선)
 * 중 고른 한 노선의 최신 수집 위치만 받아 보여 준다. 위치 조회가 실패해도 이 영역 안에서만 안내하고 화면 전체는 그대로 둔다.
 */
export function RouteMapSection({ snapshot }: { snapshot: SnapshotResponse }) {
  const headingId = useId();
  // 수집 시간 밖에는 스냅샷에 버스가 없을 수 있다. 그때만 정류장 기준정보에서 노선을 가져온다.
  const needsStationRoutes = routesFromSnapshot(snapshot).length === 0;
  const stationRoutes = useStationRoutes(needsStationRoutes ? snapshot.station.stationId : null);
  const routes = routesForMap(snapshot, stationRoutes.data);

  const [selectedRouteId, setSelectedRouteId] = useState<string | null>(null);
  const activeIndex = Math.max(
    0,
    routes.findIndex((route) => route.routeId === selectedRouteId),
  );
  const activeRoute = routes[activeIndex];
  const results = useRoutePositions(
    routes.map((route) => route.routeId),
    activeRoute?.routeId,
  );
  const activeResult = results[activeIndex];

  return (
    <section className={styles.section} aria-labelledby={headingId}>
      <h2 id={headingId} className={styles.heading}>
        {ROUTE_MAP_HEADING}
      </h2>
      {routes.length > 1 && (
        <fieldset className={styles.routePicker}>
          <legend className={styles.visuallyHidden}>{ROUTE_MAP_ROUTE_PICKER_LABEL}</legend>
          {routes.map((route, index) => {
            // 400·404 를 받은 노선은 다시 불러도 같으므로 고를 수 없게 한다. 받기 전에는 알 수 없어 고를 수 있다.
            // disabled 대신 aria-disabled 를 써서, 누른 직후 404 가 와도 키보드 초점이 사라지지 않게 한다.
            const isUnavailable = isPermanentPositionsError(results[index]?.error);
            return (
              <button
                key={route.routeId}
                type="button"
                className={styles.routeButton}
                aria-pressed={index === activeIndex}
                aria-disabled={isUnavailable || undefined}
                onClick={() => {
                  if (!isUnavailable) setSelectedRouteId(route.routeId);
                }}
              >
                {route.routeName}
                {isUnavailable && (
                  <span className={styles.routeUnavailable}> {ROUTE_MAP_ROUTE_UNAVAILABLE}</span>
                )}
              </button>
            );
          })}
        </fieldset>
      )}
      {activeRoute && activeResult ? (
        <RoutePositionsPanel
          key={activeRoute.routeId}
          route={activeRoute}
          data={activeResult.data}
          error={activeResult.error}
          isPending={activeResult.isPending}
          isFetching={activeResult.isFetching}
          onRetry={() => {
            void activeResult.refetch();
          }}
        />
      ) : (
        <p className={styles.placeholder}>
          {needsStationRoutes && stationRoutes.isPending ? ROUTE_MAP_LOADING : ROUTE_MAP_NO_ROUTES}
        </p>
      )}
    </section>
  );
}

interface RoutePositionsPanelProps {
  route: MapRoute;
  data: RoutePositionsResponse | undefined;
  error: unknown;
  isPending: boolean;
  isFetching: boolean;
  onRetry: () => void;
}

function RoutePositionsPanel({
  route,
  data,
  error,
  isPending,
  isFetching,
  onRetry,
}: RoutePositionsPanelProps) {
  const [focusRequestId, setFocusRequestId] = useState(0);
  const view = useMemo(() => (data ? toRouteMapView(data) : null), [data]);

  if (!data || !view) {
    if (isPending) return <p className={styles.placeholder}>{ROUTE_MAP_LOADING}</p>;
    return <ErrorMessage error={error} onRetry={onRetry} context="routeMap" compact />;
  }

  const routeName = data.routeName || route.routeName;
  const targetName = targetStationName(data);
  const hasMap = view.focus.length > 0;

  return (
    <div className={styles.panel}>
      <p className={styles.collected}>
        <strong>{routeName}</strong>
        {" · "}
        {data.dataUpdatedAt ? (
          <time dateTime={data.dataUpdatedAt}>
            {collectedAtText(formatKstTime(data.dataUpdatedAt))}
          </time>
        ) : (
          ROUTE_MAP_NO_RECORD
        )}
        {isFetching && <span className={styles.refreshing}> · {ROUTE_MAP_REFRESHING}</span>}
      </p>
      {!data.inCollectionWindow ? (
        <Notice tone="info" title={ROUTE_MAP_OUTSIDE_COLLECTION_TEXT.title}>
          <p>{ROUTE_MAP_OUTSIDE_COLLECTION_TEXT.description}</p>
        </Notice>
      ) : (
        data.stale && (
          <Notice tone="warning" title={ROUTE_MAP_STALE_TEXT.title}>
            <p>{ROUTE_MAP_STALE_TEXT.description}</p>
          </Notice>
        )
      )}
      {/* 갱신이 실패해도 이전 지도는 그대로 두고 위에 짧게 알린다 */}
      {error ? <ErrorMessage error={error} onRetry={onRetry} context="routeMap" compact /> : null}
      {hasMap ? (
        <>
          <RouteMap
            label={routeMapAriaLabel(routeName)}
            loadingText={ROUTE_MAP_LOADING}
            focusRequestId={focusRequestId}
            {...view}
          />
          <button
            type="button"
            className={styles.recenter}
            onClick={() => setFocusRequestId((id) => id + 1)}
          >
            {ROUTE_MAP_RECENTER}
          </button>
        </>
      ) : (
        <p className={styles.placeholder}>{ROUTE_MAP_NO_STATIONS}</p>
      )}
      <ApproachingList data={data} targetName={targetName} />
    </div>
  );
}

const SEAT_TONE_CLASS = {
  available: styles.seatsAvailable,
  noSeat: styles.seatsNoSeat,
  unknown: styles.seatsUnknown,
} as const;

function ApproachingList({
  data,
  targetName,
}: {
  data: RoutePositionsResponse;
  targetName: string;
}) {
  const headingId = useId();
  const vehicles = approachingVehicles(data.vehicles);
  return (
    <div className={styles.approaching}>
      <h3 id={headingId} className={styles.subheading}>
        {APPROACHING_HEADING}
      </h3>
      {vehicles.length === 0 ? (
        <p className={styles.empty}>{APPROACHING_EMPTY}</p>
      ) : (
        <ol className={styles.vehicleList} aria-labelledby={headingId}>
          {vehicles.map((vehicle) => (
            <li key={vehicle.vehicleId} className={styles.vehicleItem}>
              <span>{stopsToTargetText(targetName, vehicle.stopsToTarget)}</span>
              {" · "}
              <span className={cx(styles.seats, SEAT_TONE_CLASS[seatTone(vehicle.remainSeats)])}>
                {remainSeatsText(vehicle.remainSeats)}
              </span>
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
