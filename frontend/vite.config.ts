import { execSync } from "node:child_process";
import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Short git SHA captured when the dev server / build starts, so the running UI
// can tell when the API server has drifted to a different version.
function gitSha(): string {
  try {
    return execSync("git rev-parse --short HEAD").toString().trim();
  } catch {
    return "unknown";
  }
}

export default defineConfig({
  plugins: [react()],
  define: { __APP_VERSION__: JSON.stringify(gitSha()) },
  server: {
    host: true, // listen on 0.0.0.0 so the dev server is reachable over the LAN
    proxy: { "/api": "http://localhost:8000" },
  },
  test: { environment: "jsdom", globals: true },
});
