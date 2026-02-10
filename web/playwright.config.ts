import { defineConfig } from "@playwright/test";

export default defineConfig({
  testDir: "./e2e",
  timeout: 30_000,
  retries: 1,
  use: {
    baseURL: "http://localhost:3120",
    screenshot: "only-on-failure",
    trace: "on-first-retry",
    navigationTimeout: 15_000,
  },
  projects: [
    { name: "chromium", use: { browserName: "chromium" } },
  ],
  webServer: {
    command: "npm run build && npm run start",
    port: 3120,
    reuseExistingServer: true,
    timeout: 60_000,
  },
});
