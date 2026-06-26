import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": "http://localhost:8000" } },
  // @ts-ignore — vitest adds `test` at runtime; tsc doesn't see it
  test: { environment: "jsdom", globals: true },
});
