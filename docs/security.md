# Security

## Upload handling

Route files arrive from strangers and the libraries that read them are written
for correctness on well-formed input, not for hostility. Every check runs before
a parser sees the bytes: `apps/api/src/contour_api/io/validation.py`.

- **Format from content, never from the name.** The extension and the
  client-supplied content type are both attacker-controlled. A file named
  `route.gpx` containing JSON is a mistake or a probe, not a format to guess at.
- **Size limit before parsing** — 64 MB.
- **Nesting depth limit** — 100 elements.
- **XML declarations refused, not stripped.** Silently altering a user's file to
  make it safe is worse than telling them it was refused.
- **Coordinate range validation** — out-of-range values usually mean latitude and
  longitude are swapped, and the error says so.

### What was measured, not assumed

The XML guard was written against the behaviour of the installed stack
(gpxpy 1.6.2 with lxml 5.x), and measuring it changed what the code claims:

| Vector | Result |
| --- | --- |
| External entity (`file:///etc/passwd`) | **Not exploitable.** lxml raises "Entity 'xxe' not defined" rather than reading the file |
| Internal entity expansion | **Live.** A nested definition expanded to 1,000 characters, ×10 per level |
| Standard-library fallback | Parses `DOCTYPE` without complaint |

So the entity guard earns its place on the expansion vector today, and the
DOCTYPE guard is defence in depth: the lxml protection is a property of an
optional dependency, and gpxpy falls back to `xml.etree.ElementTree` when it is
absent. Contour should not depend on which XML backend happens to be installed
for whether it can be made to read local files.

## Web

Security headers are set per request in `apps/web/src/middleware.ts`, and the
policy is widened only for a basemap provider that is actually configured —
`connect-src` and `img-src` name the API origin and the map host explicitly,
with `object-src 'none'`, `frame-ancestors 'none'`, `base-uri 'self'` and no
`unsafe-eval`.

### `script-src` permits inline scripts, deliberately

Next emits inline bootstrap scripts to hydrate the page. Blocking them produces
a page whose server-rendered HTML looks perfect and in which nothing works — no
button, no form, no map mode. That is exactly what shipped here until an
end-to-end test asserting `aria-pressed` changes on click caught it. The
server-rendered output gave no hint; only interaction did.

The correct fix is a per-request nonce, and it was attempted in full: nonce on
the response CSP, the same CSP on the request headers so Next can read it,
`'strict-dynamic'`, and `force-dynamic` rendering so the HTML is not baked at
build time. With Next 16.2 the framework still did not stamp the nonce onto its
own script tags — no `nonce=` appeared in the served HTML — so `'strict-dynamic'`
refused every chunk and the page stayed inert.

`'unsafe-inline'` is therefore a measured trade, and the cost is worth stating
plainly: it permits inline script execution, which matters if an attacker can
inject markup into the document. Contour renders no user-controlled HTML — route
names, source descriptions and licence text all go through React's text
interpolation, which escapes them. `'strict-dynamic'` is deliberately absent so
host allowlisting still applies and `'self'` continues to restrict where scripts
may load from.

This should be revisited when Next restores nonce propagation.

CORS on the API names the web origin explicitly rather than reflecting the
request origin, which would defeat the purpose.

## Secrets

- `CONTOUR_SECRET_KEY` signs sessions and share tokens.
- `CONTOUR_TOKEN_ENCRYPTION_KEY` encrypts external account tokens at rest
  (Fernet). Tokens are never logged.
- Passwords are Argon2id hashes; no reversible credential is stored.
- The default `CONTOUR_SECRET_KEY` is a visible placeholder, and is treated as a
  configuration error outside development.

## Access control

- Routes are private by default. Publication is an explicit act by the owner.
- Identifiers are UUIDs rather than sequential integers, so one private route
  cannot be found from another and the catalogue size does not leak.
- Licence restrictions are enforced server-side before export or publish, and are
  distinct from permission errors: the user is allowed, the *data* is not.

## Not yet implemented

Stated plainly rather than implied by omission:

- Rate limiting is designed but not deployed.
- Third-party account connectors are interface-only; no OAuth flow is built,
  because it needs real application credentials.
- No penetration test has been performed.
