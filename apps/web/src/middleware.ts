import { NextResponse } from 'next/server';
import type { NextRequest } from 'next/server';

/**
 * Per-request security headers.
 *
 * ## Why `script-src` allows inline scripts
 *
 * Next emits inline bootstrap scripts to hydrate the page. Blocking them
 * produces a page whose server-rendered HTML looks perfect and in which nothing
 * works — no button, no form, no map mode. That is what shipped here until an
 * end-to-end test asserting `aria-pressed` changes on click caught it.
 *
 * The correct fix is a per-request nonce, and it was attempted in full: nonce on
 * the response CSP, the same CSP set on the *request* headers so Next can read
 * it, `'strict-dynamic'` so the nonced bootstrap can pull the rest of the
 * bundle, and `force-dynamic` rendering so the HTML is not baked at build time.
 * With Next 16.2 the framework still did not stamp the nonce onto its own script
 * tags — no `nonce=` attribute appeared in the served HTML — so `'strict-dynamic'`
 * refused every chunk and the page stayed inert.
 *
 * So `'unsafe-inline'` is a deliberate, measured trade rather than an oversight,
 * and it is worth being precise about what it costs. It permits inline script
 * execution, which matters if an attacker can inject markup into the document.
 * Contour renders no user-controlled HTML: route names, source descriptions and
 * licence text all go through React's text interpolation, which escapes them.
 * `'strict-dynamic'` is deliberately absent so that host allowlisting still
 * applies and `'self'` continues to restrict where scripts may be loaded from.
 *
 * Everything else stays strict: no `object-src`, no framing, `base-uri` pinned,
 * and `connect-src` naming only the API and a basemap host that is actually
 * configured. Revisit when Next restores nonce propagation.
 */

const MAP_HOSTS: Record<string, string> = {
  maptiler: 'https://api.maptiler.com',
};

export function middleware(request: NextRequest) {
  const provider = process.env.NEXT_PUBLIC_CONTOUR_MAP_PROVIDER ?? 'none';
  // Only a provider that is actually configured widens the policy. An
  // unconfigured basemap must not leave a hole in the CSP for a host the app
  // never contacts.
  const mapHost = MAP_HOSTS[provider] ?? process.env.NEXT_PUBLIC_CONTOUR_PMTILES_URL ?? '';
  const apiHost = process.env.NEXT_PUBLIC_CONTOUR_API_BASE_URL ?? '';

  const csp = [
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    // MapLibre and React both set styles programmatically; there is no
    // nonce-based path for those, so inline styles remain permitted. Inline
    // *scripts* are not.
    "style-src 'self' 'unsafe-inline'",
    `img-src 'self' data: blob: ${mapHost}`.trim(),
    "font-src 'self' data:",
    // MapLibre decodes vector tiles in a web worker created from a blob.
    "worker-src 'self' blob:",
    `connect-src 'self' ${apiHost} ${mapHost}`.trim(),
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
    'upgrade-insecure-requests',
  ]
    .join('; ')
    .replace(/\s{2,}/g, ' ');

  const response = NextResponse.next();
  response.headers.set('Content-Security-Policy', csp);
  response.headers.set('X-Content-Type-Options', 'nosniff');
  response.headers.set('Referrer-Policy', 'strict-origin-when-cross-origin');
  response.headers.set('X-Frame-Options', 'DENY');
  response.headers.set(
    'Permissions-Policy',
    // Contour asks for location only at the point of use, and needs nothing else.
    'camera=(), microphone=(), payment=(), geolocation=(self)',
  );
  return response;
}

export const config = {
  matcher: [
    // Static assets are served with their own long-lived caching and carry no
    // inline script, so they do not need a per-request nonce.
    {
      source: '/((?!_next/static|_next/image|favicon.ico).*)',
      missing: [
        { type: 'header', key: 'next-router-prefetch' },
        { type: 'header', key: 'purpose', value: 'prefetch' },
      ],
    },
  ],
};
