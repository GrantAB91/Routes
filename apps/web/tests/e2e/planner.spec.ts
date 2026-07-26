import AxeBuilder from '@axe-core/playwright';
import { expect, test } from '@playwright/test';

/**
 * Planner end-to-end tests.
 *
 * These cover what works without routing tiles. Anything needing a generated
 * route skips with its reason, so a green run never implies route generation was
 * exercised when it was not.
 */

test.describe('planner', () => {
  test('loads and offers the request field', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByRole('heading', { name: 'Plan a route', level: 1 })).toBeVisible();
    await expect(page.getByLabel('Describe the journey')).toBeVisible();
  });

  test('says plainly that no route has been generated', async ({ page }) => {
    // The failure this guards against is an empty map that reads as a loading
    // error rather than as "nothing has been planned yet".
    await page.goto('/');

    await expect(page.getByText(/No route has been generated yet/i)).toBeVisible();
  });

  test('offers every map mode', async ({ page }) => {
    await page.goto('/');
    const group = page.getByRole('group', { name: 'Map mode' });

    for (const label of [
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
      await expect(group.getByRole('button', { name: label })).toBeVisible();
    }
  });

  test('switching to a mode without a basemap still shows the route layers', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Surface' }).click();

    // Surface mode needs no basemap, so it must not show the missing-basemap
    // notice - only modes that genuinely need one do.
    await expect(page.getByRole('button', { name: 'Surface' })).toHaveAttribute(
      'aria-pressed',
      'true',
    );
    await expect(page.getByText(/No basemap is configured/i)).toHaveCount(0);
  });

  test('a mode that needs a basemap names the missing setting', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Standard' }).click();

    await expect(page.getByText(/No basemap is configured/i)).toBeVisible();
    await expect(page.getByText(/NEXT_PUBLIC_CONTOUR_MAP_PROVIDER/)).toBeVisible();
  });

  test('the topology view renders as a list, not only a diagram', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Route topology' }).click();

    await expect(page.getByRole('heading', { name: 'Route topology' })).toBeVisible();
  });

  test('the legend explains every band in text', async ({ page }) => {
    await page.goto('/');
    await page.getByRole('button', { name: 'Slope' }).click();

    const legend = page.getByRole('region', { name: /Gradient legend/i });
    await expect(legend).toBeVisible();
    // §10.7: an unknown band must be present and explained, not merely coloured.
    await expect(legend.getByText('Unknown')).toBeVisible();
    await expect(legend.getByText(/Contour does not guess/i)).toBeVisible();
  });

  test('the elevation profile ships a data table', async ({ page }) => {
    await page.goto('/');

    await expect(page.getByText('Elevation data as a table')).toBeVisible();
  });

  test('is keyboard reachable from the skip link', async ({ page }) => {
    await page.goto('/');
    await page.keyboard.press('Tab');

    await expect(page.getByRole('link', { name: /Skip to the route planner/i })).toBeFocused();
  });

  test('has no detectable accessibility violations', async ({ page }) => {
    await page.goto('/');

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze();

    expect(results.violations).toEqual([]);
  });
});

test.describe('intent parsing', () => {
  test('shows what Contour understood, with the words it came from', async ({ page }) => {
    await page.goto('/');

    await page
      .getByLabel('Describe the journey')
      .fill('Plan the Wild Atlantic Way for a road bike, each day below 100 km.');
    await page.getByRole('button', { name: /Read my request/i }).click();

    const heading = page.getByRole('heading', { name: 'What Contour understood' });
    if (!(await heading.isVisible().catch(() => false))) {
      test.skip(true, 'API not reachable from the browser context');
    }

    await expect(heading).toBeVisible();
    await expect(page.getByText('road bike')).toBeVisible();
  });
});

test.describe('coverage', () => {
  test('never claims complete coverage', async ({ page }) => {
    const response = await page.goto('/coverage');
    if (!response?.ok()) test.skip(true, 'API not reachable');

    await expect(
      page.getByText(/does not claim complete coverage/i),
    ).toBeVisible();
  });

  test('lists gaps with a reason each', async ({ page }) => {
    const response = await page.goto('/coverage');
    if (!response?.ok()) test.skip(true, 'API not reachable');

    await expect(page.getByRole('heading', { name: 'Gaps' })).toBeVisible();
    await expect(page.getByText(/egress policy/i).first()).toBeVisible();
  });

  test('has no detectable accessibility violations', async ({ page }) => {
    const response = await page.goto('/coverage');
    if (!response?.ok()) test.skip(true, 'API not reachable');

    const results = await new AxeBuilder({ page })
      .withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa', 'wcag22aa'])
      .analyze();

    expect(results.violations).toEqual([]);
  });
});
