import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { SERVICE_OUTSIDE_TITLE, SERVICE_STATE_TEXT } from "../../../lib/labels";
import { ServiceNotice } from "./ServiceNotice";

describe("ServiceNotice", () => {
  it("outside_collection: 예보 시간이 아니라는 안내와 다음 예보 시작", () => {
    render(
      <ServiceNotice
        service={{
          state: "outside_collection",
          message: "지금은 예보 시간이 아니에요",
          nextForecastStartAt: "2026-10-08T05:45:00+09:00",
        }}
      />,
    );
    expect(screen.getByText(SERVICE_OUTSIDE_TITLE)).toBeInTheDocument();
    expect(screen.getByText(SERVICE_STATE_TEXT.outside_collection)).toBeInTheDocument();
    expect(screen.getByText("10월 8일(목) 05:45")).toBeInTheDocument();
  });

  it("outside_forecast_hours: 지금 예보할 버스가 없다는 안내", () => {
    render(
      <ServiceNotice
        service={{
          state: "outside_forecast_hours",
          message: "지금은 예보 시간이 아니에요",
          nextForecastStartAt: "2026-10-08T05:45:00+09:00",
        }}
      />,
    );
    expect(screen.getByText(SERVICE_OUTSIDE_TITLE)).toBeInTheDocument();
    expect(screen.getByText(SERVICE_STATE_TEXT.outside_forecast_hours)).toBeInTheDocument();
  });

  it("in_service 면 아무것도 그리지 않는다", () => {
    const { container } = render(
      <ServiceNotice service={{ state: "in_service", message: null, nextForecastStartAt: null }} />,
    );
    expect(container).toBeEmptyDOMElement();
  });
});
