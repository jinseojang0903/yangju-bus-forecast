import type { ServiceInfo } from "../../../api/types";
import { Notice } from "../../../components/Notice";
import {
  NEXT_FORECAST_START_LABEL,
  NEXT_FORECAST_START_UNKNOWN,
  SERVICE_OUTSIDE_TITLE,
  SERVICE_STATE_TEXT,
} from "../../../lib/labels";
import { formatKstDateTime } from "../../../lib/time";

/**
 * 서비스 시간 밖(계약 2.2절) 안내. 오류가 아니라 정상 상태이므로 경고색을 쓰지 않는다.
 * in_service 이면 아무것도 그리지 않는다.
 */
export function ServiceNotice({ service }: { service: ServiceInfo }) {
  if (service.state === "in_service") return null;
  return (
    <Notice tone="info" title={SERVICE_OUTSIDE_TITLE}>
      <p>{SERVICE_STATE_TEXT[service.state]}</p>
      {service.nextForecastStartAt ? (
        <p>
          {NEXT_FORECAST_START_LABEL}{" "}
          <time dateTime={service.nextForecastStartAt}>
            {formatKstDateTime(service.nextForecastStartAt)}
          </time>
        </p>
      ) : (
        <p>{NEXT_FORECAST_START_UNKNOWN}</p>
      )}
    </Notice>
  );
}
