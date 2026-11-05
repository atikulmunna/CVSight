import { defineConfig } from "@playwright/test";

// Its own port, so a development API on the usual one is never used by mistake.
const API_PORT = "47801";

// scripts/e2e.ps1 prepares the database, media folders and account these servers use.
export default defineConfig({
  testDir: "./e2e",
  forbidOnly: true,
  use: {
    baseURL: "http://127.0.0.1:4173",
    browserName: "chromium",
    viewport: { width: 1440, height: 900 },
    trace: "retain-on-failure",
  },
  webServer: [
    {
      command: "uv run python -m shelfsight_api",
      cwd: "..",
      url: `http://127.0.0.1:${API_PORT}/api/health`,
      env: { SHELFSIGHT_API_PORT: API_PORT },
      timeout: 60_000,
    },
    {
      command: "npm run preview",
      url: "http://127.0.0.1:4173",
      env: { SHELFSIGHT_API_PORT: API_PORT },
    },
  ],
});
