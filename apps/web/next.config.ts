import type { NextConfig } from 'next';

const config: NextConfig = {
  reactStrictMode: true,
  // Contour's own layers render without a basemap, so the app must not assume
  // any external host is reachable. A strict CSP is set in the response headers
  // and widened only for a basemap provider that is actually configured.
  async headers() {
    const mapHost = process.env.NEXT_PUBLIC_CONTOUR_MAP_PROVIDER === 'maptiler'
      ? 'https://api.maptiler.com'
      : '';
    return [
      {
        source: '/:path*',
        headers: [
          { key: 'X-Content-Type-Options', value: 'nosniff' },
          { key: 'Referrer-Policy', value: 'strict-origin-when-cross-origin' },
          { key: 'X-Frame-Options', value: 'DENY' },
          {
            key: 'Content-Security-Policy',
            value: [
              "default-src 'self'",
              "img-src 'self' data: blob:" + (mapHost ? ` ${mapHost}` : ''),
              "worker-src 'self' blob:",
              `connect-src 'self' ${process.env.NEXT_PUBLIC_CONTOUR_API_BASE_URL ?? ''} ${mapHost}`.trim(),
              "style-src 'self' 'unsafe-inline'",
              "script-src 'self'",
              "object-src 'none'",
              "base-uri 'self'",
              "frame-ancestors 'none'",
            ].join('; '),
          },
        ],
      },
    ];
  },
};

export default config;
