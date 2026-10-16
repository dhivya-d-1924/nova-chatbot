// End-to-end tests against the real backend (with a fake AI provider) serving the built frontend.
// Run: npm run test:e2e   (set E2E_PYTHON to your backend virtualenv's python if it isn't "python3")
// Chromium projects run everywhere; Firefox and WebKit (Safari engine) run when E2E_ALL_BROWSERS=1 (CI installs them).
import { defineConfig, devices } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT || 5056);
const python = process.env.E2E_PYTHON || "python3";
const chromiumPath = process.env.PLAYWRIGHT_CHROMIUM_PATH;
const launch = chromiumPath ? { launchOptions: { executablePath: chromiumPath } } : {};
const phone = (name, width, height, extra = {}) => ({
  name, use: { browserName: "chromium", viewport: { width, height }, isMobile: true, hasTouch: true, deviceScaleFactor: 2, ...launch, ...extra },
});

const projects = [
  { name: "desktop-chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 800 }, ...launch } },
  phone("mobile-320", 320, 568, { userAgent: devices["iPhone SE"].userAgent }),
  phone("android-pixel", 393, 851, { userAgent: devices["Pixel 7"].userAgent }),
  phone("iphone-portrait", 390, 844, { userAgent: devices["iPhone 13"].userAgent }),
  phone("phone-landscape", 844, 390, { userAgent: devices["iPhone 13"].userAgent }),
  { name: "tablet-ipad", use: { browserName: "chromium", viewport: { width: 820, height: 1180 }, hasTouch: true, isMobile: true,
    userAgent: devices["iPad (gen 7)"].userAgent, ...launch } },
];
if (process.env.E2E_ALL_BROWSERS) {
  projects.push(
    { name: "desktop-firefox", use: { ...devices["Desktop Firefox"] } },
    { name: "desktop-webkit", use: { ...devices["Desktop Safari"] } },
    { name: "iphone-webkit", use: { ...devices["iPhone 13"] } },
    { name: "edge", use: { ...devices["Desktop Edge"], channel: "msedge" } },
  );
}

export default defineConfig({
  testDir: "./e2e",
  globalSetup: "./e2e/global-setup.js",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  fullyParallel: true,
  workers: process.env.CI ? 2 : 3,
  retries: process.env.CI ? 1 : 0,
  reporter: [["list"], ["html", { open: "never" }]],
  use: { baseURL: `http://localhost:${PORT}`, trace: "retain-on-failure", screenshot: "only-on-failure" },
  projects,
  webServer: {
    command: `${python} ../backend/tests/e2e_server.py ${PORT}`,
    url: `http://localhost:${PORT}/api/health/ready`,
    reuseExistingServer: false,
    timeout: 60_000,
    stdout: "ignore",
    stderr: "pipe",
  },
});
