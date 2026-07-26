import { defineConfig, devices } from '@playwright/test';

/**
 * End-to-end configuration.
 *
 * Desktop and mobile are both first-class (§22.4), so the same specs run in both
 * projects rather than mobile being a separate, thinner suite.
 */
export default defineConfig({
  testDir: './tests',
  testMatch: ['e2e/**/*.spec.ts', 'acceptance/**/*.spec.ts'],
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 1 : 0,
  reporter: process.env.CI ? [['github'], ['html', { open: 'never' }]] : 'list',
  use: {
    baseURL: process.env.CONTOUR_WEB_URL ?? 'http://127.0.0.1:3000',
    trace: 'retain-on-failure',
  },
  projects: [
    {
      name: 'desktop',
      use: {
        ...devices['Desktop Chrome'],
        // CONTOUR_CHROMIUM_PATH points at a Chromium already present on the
        // host. Environments that ship one (CI images, sandboxes) set it rather
        // than downloading a second copy for a pinned Playwright version.
        launchOptions: process.env.CONTOUR_CHROMIUM_PATH
          ? { executablePath: process.env.CONTOUR_CHROMIUM_PATH }
          : undefined,
      },
    },
    {
      name: 'mobile',
      use: {
        ...devices['Pixel 7'],
        launchOptions: process.env.CONTOUR_CHROMIUM_PATH
          ? { executablePath: process.env.CONTOUR_CHROMIUM_PATH }
          : undefined,
      },
    },
  ],
  webServer: process.env.CONTOUR_WEB_URL
    ? undefined
    : {
        command: 'pnpm start',
        url: 'http://127.0.0.1:3000',
        reuseExistingServer: !process.env.CI,
        timeout: 120_000,
      },
});
