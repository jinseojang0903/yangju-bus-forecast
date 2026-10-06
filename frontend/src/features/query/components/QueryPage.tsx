import { useId, useState } from "react";
import { useNavigate } from "react-router";
import type { DestinationId, ParseQueryResponse, StationId } from "../../../api/types";
import { ErrorMessage } from "../../../components/ErrorMessage";
import { toForecastSearch } from "../../forecast/snapshotRequest";
import { useStationRoutes } from "../hooks/useStationRoutes";
import { useStations } from "../hooks/useStations";
import { resolveChoice, toTimeOfDay } from "../selection";
import { NaturalLanguageQuery } from "./NaturalLanguageQuery";
import styles from "./QueryPage.module.css";

/** 조건 입력(F01). 정류장·목적지·도착 마감을 고르고 예보 화면으로 넘어간다(새로고침 없음). */
export function QueryPage() {
  const navigate = useNavigate();
  const titleId = useId();
  const stationFieldId = useId();
  const destinationFieldId = useId();
  const deadlineFieldId = useId();
  const deadlineHintId = useId();

  const [stationChoice, setStationChoice] = useState<StationId | null>(null);
  const [destinationChoice, setDestinationChoice] = useState<DestinationId | null>(null);
  const [deadlineInput, setDeadlineInput] = useState("");

  const stationsQuery = useStations();
  const stations = stationsQuery.data?.items ?? [];
  const stationId = resolveChoice(
    stationChoice,
    stations.map((station) => station.stationId),
  );

  const routesQuery = useStationRoutes(stationId);
  const destinations = routesQuery.data?.destinations ?? [];
  const destinationId = resolveChoice(
    destinationChoice,
    destinations.map((destination) => destination.destinationId),
  );
  const selectedDestination =
    destinations.find((destination) => destination.destinationId === destinationId) ?? null;

  const canSubmit = stationId !== null && destinationId !== null;

  const goToForecast = () => {
    if (stationId === null || destinationId === null) return;
    const search = toForecastSearch({
      station: stationId,
      destination: destinationId,
      deadline: toTimeOfDay(deadlineInput),
    });
    navigate(`/forecast?${search}`);
  };

  const applyParsedQuery = (query: ParseQueryResponse["query"]) => {
    if (query.station) {
      setStationChoice(query.station);
      setDestinationChoice(null);
    }
    if (query.destination) setDestinationChoice(query.destination);
    if (query.deadline) setDeadlineInput(query.deadline);
  };

  return (
    <>
      <section className={styles.card} aria-labelledby={titleId}>
        <h1 id={titleId} className={styles.title}>
          어디서 어디로 가세요?
        </h1>
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            goToForecast();
          }}
        >
          <div className={styles.field}>
            <label htmlFor={stationFieldId} className={styles.label}>
              출발 정류장
            </label>
            <select
              id={stationFieldId}
              className={styles.control}
              value={stationId ?? ""}
              disabled={stations.length === 0}
              onChange={(event) => {
                setStationChoice(event.target.value);
                setDestinationChoice(null);
              }}
            >
              {stations.length === 0 && (
                <option value="">
                  {stationsQuery.isPending ? "불러오는 중…" : "고를 수 있는 정류장이 없어요"}
                </option>
              )}
              {stations.map((station) => (
                <option key={station.stationId} value={station.stationId}>
                  {`${station.name} (${station.directionLabel})`}
                </option>
              ))}
            </select>
            {stationsQuery.error && (
              <ErrorMessage
                error={stationsQuery.error}
                onRetry={() => {
                  void stationsQuery.refetch();
                }}
              />
            )}
          </div>

          <div className={styles.field}>
            <label htmlFor={destinationFieldId} className={styles.label}>
              목적지
            </label>
            <select
              id={destinationFieldId}
              className={styles.control}
              value={destinationId ?? ""}
              disabled={destinations.length === 0}
              onChange={(event) => setDestinationChoice(event.target.value)}
            >
              {destinations.length === 0 && (
                <option value="">
                  {stationId !== null && routesQuery.isFetching
                    ? "불러오는 중…"
                    : "고를 수 있는 목적지가 없어요"}
                </option>
              )}
              {destinations.map((destination) => (
                <option key={destination.destinationId} value={destination.destinationId}>
                  {destination.name}
                </option>
              ))}
            </select>
            {selectedDestination && (
              <p className={styles.hint}>
                이용 노선: {selectedDestination.routes.map((route) => route.routeName).join(", ")}
              </p>
            )}
            {routesQuery.error && (
              <ErrorMessage
                error={routesQuery.error}
                onRetry={() => {
                  void routesQuery.refetch();
                }}
              />
            )}
          </div>

          <div className={styles.field}>
            <label htmlFor={deadlineFieldId} className={styles.label}>
              도착 마감 (선택)
            </label>
            <div className={styles.inline}>
              <input
                id={deadlineFieldId}
                className={styles.control}
                type="time"
                step={60}
                value={deadlineInput}
                aria-describedby={deadlineHintId}
                onChange={(event) => setDeadlineInput(event.target.value)}
              />
              {deadlineInput && (
                <button
                  type="button"
                  className={styles.secondaryButton}
                  onClick={() => setDeadlineInput("")}
                >
                  지우기
                </button>
              )}
            </div>
            <p id={deadlineHintId} className={styles.hint}>
              목적지에 이 시각까지 도착해야 하면 넣어 주세요. 대안 버스를 이 시각 기준으로 비교해요.
            </p>
          </div>

          <button type="submit" className={styles.primaryButton} disabled={!canSubmit}>
            예보 보기
          </button>
        </form>
      </section>

      <NaturalLanguageQuery onParsed={applyParsedQuery} />
    </>
  );
}
