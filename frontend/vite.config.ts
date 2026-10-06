import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";

// 개발 중에는 같은 출처(/api)로 호출하고 Vite 가 백엔드로 넘긴다.
// 계약 1.2절: CORS 허용 목록에 localhost 를 넣지 않으므로 프록시가 필수다.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      "/api": {
        target: "http://127.0.0.1:8000",
        changeOrigin: false,
      },
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    restoreMocks: true,
  },
});
