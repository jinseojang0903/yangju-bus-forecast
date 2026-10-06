import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";
import { appRoutes } from "../routes";

function createTestQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: Number.POSITIVE_INFINITY, networkMode: "always" },
      mutations: { retry: false, networkMode: "always" },
    },
  });
}

/** 앱 전체 라우트를 메모리 라우터로 그린다. router.state.location 으로 이동을 확인한다. */
export function renderApp(initialEntry: string) {
  const queryClient = createTestQueryClient();
  const router = createMemoryRouter(appRoutes, { initialEntries: [initialEntry] });
  const user = userEvent.setup();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router, user, queryClient };
}

/** 라우터(Link)와 QueryClient 가 필요한 컴포넌트 하나를 그린다. */
export function renderWithProviders(ui: ReactElement) {
  const queryClient = createTestQueryClient();
  const router = createMemoryRouter([{ path: "/", element: ui }], { initialEntries: ["/"] });
  const user = userEvent.setup();
  const view = render(
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  return { ...view, router, user, queryClient };
}
