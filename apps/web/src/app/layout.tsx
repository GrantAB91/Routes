import type { Metadata } from 'next';

import './globals.css';

export const metadata: Metadata = {
  title: 'Contour — cycling route planning',
  description:
    'Plan cycling routes from named sources, with unknown data reported rather than filled in.',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        {/* §16.10: a keyboard user must be able to reach the map without
            tabbing through the whole planning panel first. */}
        <a className="skip-link" href="#main">
          Skip to the route planner
        </a>
        <header className="app-header">
          <a href="/" className="app-header__brand">
            Contour
          </a>
          <nav aria-label="Main">
            <a href="/">Plan</a>
            <a href="/coverage">Coverage</a>
          </nav>
        </header>
        <main id="main">{children}</main>
      </body>
    </html>
  );
}
