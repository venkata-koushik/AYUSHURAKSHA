// @ts-check
const { defineConfig } = require("@playwright/test");

module.exports = defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 8_000 },
  fullyParallel: false,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8000",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    video: "retain-on-failure",
    actionTimeout: 12_000,
    navigationTimeout: 25_000,
  },
  projects: [
    {
      name: "chromium-headless",
      use: { browserName: "chromium", headless: true },
    },
    {
      name: "chromium-headed",
      use: { browserName: "chromium", headless: false },
    },
  ],
});
