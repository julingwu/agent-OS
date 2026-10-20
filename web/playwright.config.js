import { defineConfig } from "@playwright/test";
import { createHash } from "node:crypto";
import os from "node:os";
import path from "node:path";

const target = new URL(process.env.AGENT_OS_TEST_URL || "http://localhost:8078");
if (target.protocol !== "http:" || target.hostname !== "localhost" || !target.port || target.port === "8077" || target.username || target.password) {
  throw new Error("Browser tests require an isolated http://localhost:<port> server; live port 8077 is prohibited.");
}
const projectKey = createHash("sha256").update(import.meta.dirname).digest("hex").slice(0, 10);

// Start a separate mock server before invoking this suite. CI must use a fresh
// DB/workspace, UI_DEV_LOGIN=1, UI_RUNNER=mock_acp, AUTO_APPROVE=0, EVAL_JUDGE=0,
// disabled background integrations, and empty model API keys. Never reuse the
// normal desktop backend or its database. No webServer entry starts live state.
export default defineConfig({
  testDir: "./tests",
  fullyParallel: false,
  workers: 1,
  retries: 0,
  timeout: 120_000,
  expect: { timeout: 10_000 },
  reporter: "line",
  outputDir: path.join(os.tmpdir(), `agent-os-browser-tests-${projectKey}`),
  use: {
    baseURL: target.origin,
    browserName: "chromium",
    headless: true,
    viewport: { width: 1440, height: 1000 },
    locale: "zh-CN",
    screenshot: "only-on-failure",
    trace: "retain-on-failure",
    actionTimeout: 15_000,
  },
});
