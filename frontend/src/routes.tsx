import type { RouteObject } from "react-router";
import { AppLayout } from "./components/AppLayout";
import { RouteErrorPage } from "./components/RouteErrorPage";
import { ForecastPage } from "./features/forecast/components/ForecastPage";
import { QueryPage } from "./features/query/components/QueryPage";

export const appRoutes: RouteObject[] = [
  {
    path: "/",
    element: <AppLayout />,
    errorElement: <RouteErrorPage />,
    children: [
      { index: true, element: <QueryPage /> },
      { path: "forecast", element: <ForecastPage /> },
    ],
  },
];
