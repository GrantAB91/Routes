import { existsSync } from 'node:fs';
import { join } from 'node:path';

import { defineConfig, devices } from '@playwright/test';

/**
 * Find a Chromium already on this host.
 *
 * Playwright looks for the exact build its pinned version expects. Where an
 * environment ships its own Chromium under a different build number — sandboxes
 * and CI images usually do — the suite fails to launch with a message about
 * installing browsers, which reads as a broken test suite rather than a missing
 * download. Discovering the installed binary keeps the failure honest: the tests
 * either run, or they say the browser is genuinely absent.
 */
function chromiumPath(): string | undefined {
  if (process.env.CONTOUR_CHROMIUM_PATH) return process.env.CONTOUR_CHROMIUM_PATH;

  const root = process.env.PLAYWRIGHT_BROWSERS_PATH;
  if (!root) return undefined;

  // The symlink first, then the versioned layout Playwright writes.
  const candidates = [join(root, 'chromium'), join(root, 'chromium', 'chrome-linux', 'chrome')];
  return candidates.find((candidate) => existsSync(candidate));
}

const CHROMIUM = chromiumPath();

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
        launchOptions: CHROMIUM ? { executablePath: CHROMIUM } : undefined,
      },
    },
    {
      name: 'mobile',
      use: {
        ...devices['Pixel 7'],
        launchOptions: CHROMIUM ? { executablePath: CHROMIUM } : undefined,
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
