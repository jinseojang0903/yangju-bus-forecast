import type { SnapshotRequest } from "./endpoints";
import type { RouteId, SnapshotId, StationId } from "./types";

export const queryKeys = {
  health: () => ["health"] as const,
  stations: () => ["stations"] as const,
  stationRoutes: (stationId: StationId) => ["stations", stationId, "routes"] as const,
  snapshot: (request: SnapshotRequest) =>
    [
      "snapshot",
      request.station,
      request.destination,
      request.deadline ?? null,
      request.scenario ?? null,
    ] as const,
  explanation: (snapshotId: SnapshotId) => ["explanation", snapshotId] as const,
  routePositions: (routeId: RouteId) => ["routes", routeId, "positions"] as const,
};
