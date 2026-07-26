# Contour

Cycling route planning that names its sources and says plainly what it does not
know.

Contour is built around one idea: a route planner is only as trustworthy as its
handling of missing data. Most tools quietly fill gaps — an untagged lane becomes
"paved", an unsurveyed road becomes "legal", a DEM hole becomes a flat section —
and the result looks authoritative while being partly invented. Contour keeps
unknown as a value, reports it everywhere a figure depends on it, and refuses to
present a constraint as met when the data needed to check it does not exist.

## What works today

| Capability | State |
| --- | --- |
| PostGIS schema, 29 tables, migrations verified by round trip | Working |
| Valhalla 3.8.3 built from source, running, adapter tested live | Working |
| Elevation analysis: relief, gradient windows, noise filtering, climb detection | Working |
| Constraint evaluation, route validation, four-state feasibility verdict | Working |
| Solve → validate → re-solve loop, alternative generation | Working |
| Natural language intent parsing | Working |
| Upload validation, GPX import and export with round-trip tests | Working |
| Multi-day stage planning and rebalancing | Working |
| Source registry, licence engine, coverage reporting | Working |
| Web app: planner, coverage screen, elevation profile, topology view, 12 map modes | Working |
| **Route generation on real geography** | **Blocked — needs routing tiles** |
| **Wild Atlantic Way reference project** | **Blocked — needs the official geometry** |

The blocked items are blocked on data, not code. See
[Getting real data in](#getting-real-data-in).

## Running it

Requires PostgreSQL 16 with PostGIS, Redis, Node 22+, pnpm, Python 3.12+ and uv.

```bash
# Build the routing engine from source (~20-45 minutes)
sudo ./infra/valhalla/build.sh
./infra/valhalla/make-config.sh

# Start PostGIS, Redis and Valhalla
./infra/scripts/dev-up.sh

# Database
cp .env.example .env          # then edit
pnpm db:upgrade

# API and web app
pnpm install
pnpm api                      # http://127.0.0.1:8000  (/docs for OpenAPI)
pnpm dev                      # http://127.0.0.1:3000
```

`dev-up.sh` reports what is actually available, including saying plainly when no
routing tiles exist. `GET /health/components` returns the same information as
JSON, distinguishing a capability that is *switched off* from one that is
*broken*.

## Getting real data in

Contour needs three things it cannot generate: a routing graph, an elevation
model, and the route sources themselves.

```bash
# Routing tiles from an OpenStreetMap extract.
# Takes a URL, or a local .pbf path where the host is unreachable.
./infra/valhalla/build-tiles.sh [/path/to/ireland-and-northern-ireland.osm.pbf]

# Elevation: place GeoTIFF or HGT tiles in CONTOUR_ELEVATION_RASTER_DIR and set
# CONTOUR_ELEVATION_DATASET_ID to the exact dataset. Without both, elevation
# reports as unknown rather than guessing.
```

Route sources are listed in [`docs/source_registry.md`](docs/source_registry.md),
which is generated from the same definition that seeds the database. Sources
whose host is unreachable are marked `implemented_blocked_egress`: the connector
exists and is tested, and nothing has been imported. That is shown as a stated
gap on the Coverage screen, never as an absence of routes in the region.

## Testing

```bash
pnpm api:test        # pytest: unit, property and integration
pnpm --filter @contour/web test   # vitest
pnpm test:e2e        # Playwright
```

Integration tests that need routing tiles skip with the reason and the command
that would fix it, rather than passing vacuously. A green suite never implies
routing was exercised when it was not.

## Documentation

| Document | Covers |
| --- | --- |
| [architecture.md](docs/architecture.md) | Structure, provider seams, the three load-bearing decisions |
| [source_registry.md](docs/source_registry.md) | Every source, its licence and its gaps (generated) |
| [source_ingestion.md](docs/source_ingestion.md) | Connectors, idempotency, provenance |
| [elevation_method.md](docs/elevation_method.md) | How every elevation figure is produced |
| [route_scoring.md](docs/route_scoring.md) | Score components and what they do not mean |
| [route_validation.md](docs/route_validation.md) | Checks, constraints, the feasibility verdict |
| [licensing_and_attribution.md](docs/licensing_and_attribution.md) | What may be exported and published |
| [security.md](docs/security.md) | Upload hardening, measured attack surface |
| [privacy.md](docs/privacy.md) | What is stored, shared and deleted |
| [accessibility.md](docs/accessibility.md) | WCAG 2.2 AA approach and keyboard testing |
| [performance_budgets.md](docs/performance_budgets.md) | Budgets and what has been measured |
| [deployment.md](docs/deployment.md) | Environments and configuration |
| [operations.md](docs/operations.md) | Backup, restore, rollback, reimport |
| [Wild_Atlantic_Way_acceptance.md](docs/Wild_Atlantic_Way_acceptance.md) | The end-to-end acceptance flow |
| [competitive_benchmark.md](docs/competitive_benchmark.md) | Benchmark method and current status |

Environment variables are documented in [`.env.example`](.env.example); every
optional setting states what stops working without it.

## What Contour does not claim

- It does not assess whether a route is safe. It reports observable, sourced
  attributes — road class, surface, access, gradient, infrastructure — and
  nothing here should be read as a safety judgement.
- It holds no traffic data. Where a preference asks to reduce traffic exposure,
  road class is used as a labelled proxy and reported as one.
- It does not claim complete coverage of cycling routes in any region. A route
  absent from the catalogue is absent because no connected source published it.

## Licence

AGPL-3.0-or-later. Data from external sources remains under its own licence;
see [`docs/licensing_and_attribution.md`](docs/licensing_and_attribution.md).
