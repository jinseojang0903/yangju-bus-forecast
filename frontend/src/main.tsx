import { QueryClientProvider } from "@tanstack/react-query";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { createBrowserRouter } from "react-router";
import { RouterProvider } from "react-router/dom";
import { createQueryClient } from "./api/queryClient";
import "./index.css";
import { appRoutes } from "./routes";

const rootElement = document.getElementById("root");
if (!rootElement) {
  throw new Error("index.html 에 #root 요소가 없습니다");
}

const queryClient = createQueryClient();
const router = createBrowserRouter(appRoutes);

createRoot(rootElement).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
