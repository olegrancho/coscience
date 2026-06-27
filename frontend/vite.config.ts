import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    host: true, // listen on 0.0.0.0 so the dev server is reachable over the LAN
    proxy: { "/api": "http://localhost:8000" },
  },
  test: { environment: "jsdom", globals: true },
});
