import type { SnapshotRequest } from "./endpoints";
import type { SnapshotId, StationId } from "./types";

export const queryKeys = {
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
};
