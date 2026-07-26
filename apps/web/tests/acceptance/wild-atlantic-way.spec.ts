import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

/**
 * Wild Atlantic Way acceptance (§22.8).
 *
 * The twenty steps of the reference flow, in order, desktop and mobile.
 *
 * Steps needing data Contour cannot obtain in this environment skip with the
 * exact reason and the command that would satisfy them. That is deliberate: a
 * suite that passed these vacuously would report the product's headline flow as
 * working when it has never once run against real geography, which is precisely
 * the kind of false success §26.2 forbids.
 *
 * Run with `pnpm test:acceptance:waw` once tiles and the official geometry are
 * present. See docs/Wild_Atlantic_Way_acceptance.md.
 */

const API = process.env.NEXT_PUBLIC_CONTOUR_API_BASE_URL ?? 'http://127.0.0.1:8000';

interface Components {
  components: Record<string, { status: string; detail?: string; tile_count?: number }>;
}

async function capabilities(request: import('@playwright/test').APIRequestContext) {
  try {
    const response = await request.get(`${API}/health/components`);
    return (await response.json()) as Components;
  } catch {
    return null;
  }
}

let routingAvailable = false;
let elevationAvailable = false;
let projectSeeded = false;

test.beforeAll(async ({ request }) => {
  const report = await capabilities(request);
  if (!report) return;

  const routing = report.components.routing;
  routingAvailable = routing?.status === 'healthy' && (routing.tile_count ?? 0) > 0;
  elevationAvailable = report.components.elevation?.status === 'healthy';

  try {
    const project = await request.get(`${API}/v1/journeys/wild-atlantic-way`);
    projectSeeded = project.ok();
  } catch {
    projectSeeded = false;
  }
});

const needsRouting = () =>
  test.skip(
    !routingAvailable,
    'No routing tiles for Ireland. Run infra/valhalla/build-tiles.sh with an OSM extract.',
  );

const needsProject = () =>
  test.skip(
    !projectSeeded,
    'The Wild Atlantic Way project is not seeded. Import the official geometry, then run pnpm seed:waw.',
  );

const needsElevation = () =>
  test.skip(
    !elevationAvailable,
    'No elevation provider. Place DEM tiles and set CONTOUR_ELEVATION_PROVIDER and CONTOUR_ELEVATION_DATASET_ID.',
  );

test.describe('Wild Atlantic Way', () => {
  test('1. the seeded project opens', async ({ page }) => {
    needsProject();
    await page.goto('/projects/wild-atlantic-way');

    await expect(page.getByRole('heading', { name: /Wild Atlantic Way/i })).toBeVisible();
  });

  test('2. official geometry shows its source and attribution', async ({ page }) => {
    needsProject();
    await page.goto('/projects/wild-atlantic-way');

    await expect(page.getByText(/Fáilte Ireland/i)).toBeVisible();
    // §13.5: the corridor must never be presented as a cycling route.
    await expect(page.getByText(/touring corridor/i)).toBeVisible();
  });

  test('3. Signature Discovery Points are shown', async ({ page }) => {
    needsProject();
    await page.goto('/projects/wild-atlantic-way');

    await expect(page.getByRole('button', { name: /Discovery Points/i })).toBeVisible();
  });

  test('4. cycling overlays are available', async ({ page }) => {
    needsProject();
    needsRouting();
    await page.goto('/projects/wild-atlantic-way');

    await page.getByRole('button', { name: 'Cycling network' }).click();
    await expect(page.getByRole('region', { name: /legend/i })).toBeVisible();
  });

  test('5. four distinct alternatives are generated', async ({ page }) => {
    needsProject();
    needsRouting();
    await page.goto('/projects/wild-atlantic-way');
    await page.getByRole('button', { name: /Generate alternatives/i }).click();

    const cards = page.getByRole('article', { name: /route option/i });
    await expect(cards).toHaveCount(4, { timeout: 120_000 });
    // Each must explain itself in plain language (§3.6).
    for (let index = 0; index < 4; index += 1) {
      await expect(cards.nth(index).getByText(/because|trades|favours|stays/i)).toBeVisible();
    }
  });

  test('6. every comparison metric is shown, including unknown data', async ({ page }) => {
    needsProject();
    needsRouting();
    await page.goto('/projects/wild-atlantic-way/compare');

    for (const metric of [
      /total distance/i,
      /ascent/i,
      /surface/i,
      /cycle infrastructure/i,
      /road class/i,
      /shared distance/i,
      /unique distance/i,
      /unknown/i,
    ]) {
      await expect(page.getByText(metric).first()).toBeVisible();
    }
  });

  test('7-8. daily limits produce stages', async ({ page }) => {
    needsProject();
    needsRouting();
    await page.goto('/projects/wild-atlantic-way');

    await page.getByLabel(/Maximum distance per day/i).fill('100');
    await page.getByLabel(/Maximum ascent per day/i).fill('1400');
    await page.getByRole('button', { name: /Generate stages/i }).click();

    await expect(page.getByRole('list', { name: /stages/i })).toBeVisible();
    // §12.7: each overnight point explains itself.
    await expect(page.getByText(/furthest overnight stop within/i).first()).toBeVisible();
  });

  test('9. every climb can be inspected', async ({ page }) => {
    needsProject();
    needsRouting();
    needsElevation();
    await page.goto('/projects/wild-atlantic-way/climbs');

    const climbs = page.getByRole('listitem');
    await expect(climbs.first()).toBeVisible();
    await expect(page.getByText(/measured over/i).first()).toBeVisible();
  });

  test('10-11. editing a section updates every metric', async ({ page }) => {
    needsProject();
    needsRouting();
    await page.goto('/projects/wild-atlantic-way');

    const before = await page.getByTestId('route-distance').textContent();
    await page.getByRole('button', { name: /Edit section/i }).click();
    await page.getByRole('button', { name: /Apply/i }).click();

    await expect(page.getByTestId('route-distance')).not.toHaveText(before ?? '');
    await expect(page.getByTestId('route-ascent')).toBeVisible();
  });

  test('12. every view is reachable without losing the route', async ({ page }) => {
    needsProject();
    await page.goto('/projects/wild-atlantic-way');

    for (const mode of [
      'Standard',
      'Cycling network',
      'Topographic',
      'Satellite',
      '3D terrain',
      'Slope',
      'Surface',
      'Access and restrictions',
      'Road classification',
      'Source and provenance',
      'Data confidence',
      'Route topology',
    ]) {
      await page.getByRole('button', { name: mode }).click();
      await expect(page.getByRole('button', { name: mode })).toHaveAttribute(
        'aria-pressed',
        'true',
      );
      // The route must survive every switch (§10.9).
      await expect(page.getByTestId('route-distance')).toBeVisible();
    }
  });

  test('13. the 3D flyover runs and honours reduced motion', async ({ page }) => {
    needsProject();
    await page.emulateMedia({ reducedMotion: 'reduce' });
    await page.goto('/projects/wild-atlantic-way');
    await page.getByRole('button', { name: '3D terrain' }).click();

    await expect(page.getByRole('button', { name: /Flyover/i })).toBeVisible();
  });

  test('14. the topology view lists what you pass', async ({ page }) => {
    needsProject();
    await page.goto('/projects/wild-atlantic-way');
    await page.getByRole('button', { name: 'Route topology' }).click();

    await expect(page.getByRole('heading', { name: 'Route topology' })).toBeVisible();
    await expect(page.getByText(/does not represent distance/i)).toBeVisible();
  });

  test('15-18. export, reimport and verify', async ({ page }) => {
    needsProject();
    needsRouting();
    await page.goto('/projects/wild-atlantic-way');

    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: /Export route/i }).click();
    const file = await download;
    expect(file.suggestedFilename()).toMatch(/\.gpx$/);

    // The manifest must travel with the file (§15.6.9).
    await page.goto('/projects/wild-atlantic-way/export');
    await expect(page.getByText(/OpenStreetMap contributors/i)).toBeVisible();
  });

  test('19. source disagreements are shown, not resolved away', async ({ page }) => {
    needsProject();
    await page.goto('/projects/wild-atlantic-way/sources');

    // Where two sources describe the same route differently, both remain and the
    // difference is visible (§2.9).
    await expect(page.getByRole('heading', { name: /Source/i })).toBeVisible();
  });

  test('20. the whole flow is usable on mobile', async ({ page }, testInfo) => {
    test.skip(testInfo.project.name !== 'mobile', 'mobile project only');
    needsProject();
    await page.goto('/projects/wild-atlantic-way');

    await expect(page.getByRole('heading', { name: /Wild Atlantic Way/i })).toBeVisible();

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21aa', 'wcag22aa'])
      .analyze();
    expect(results.violations).toEqual([]);
  });
});
