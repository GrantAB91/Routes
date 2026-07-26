import type { NextConfig } from 'next';

const config: NextConfig = {
  reactStrictMode: true,
  // Security headers, including the Content Security Policy, are set per request
  // in src/middleware.ts. They cannot live here: Next emits inline bootstrap
  // scripts that need a per-request nonce, and a static script-src blocks them,
  // producing a page that renders perfectly and does nothing.
};

export default config;
