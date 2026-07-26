# Architecture

## Shape

```
apps/web            Next 16 · React 19 · MapLibre GL 6 — the planner and coverage screens
apps/api            FastAPI · SQLAlchemy 2 · GeoAlchemy2 · Alembic — the contract and the logic
workers/ingestion   Scheduled imports and long-running jobs
services/routing    Valhalla configuration and reference config
packages/contracts  OpenAPI-generated client types
packages/map        Map provider adapters
packages/routing    Shared intent and scoring contracts
infra               Build scripts, service startup, deployment
docs                This directory
```

The API holds the logic; the web app holds none of it. Every rule about what may
be claimed — whether a constraint was met, whether a licence permits an export,
what a coverage statement may say — is decided server-side and rendered by the
client. That is deliberate: a rule enforced in two places is a rule that will
eventually be enforced differently in each.

## Provider seams

Five interfaces isolate anything that could be swapped or could be absent:

| Seam | Implementations | When unconfigured |
| --- | --- | --- |
| `RoutingProvider` | Valhalla 3.8.3 over HTTP | Routing returns 503 with the setting to fix it |
| `ElevationProvider` | Valhalla `/height`, local raster | Elevation reports unknown throughout |
| `MapProvider` | MapTiler, Protomaps, self-hosted | Blank canvas; Contour's own layers still render |
| `GeocodingProvider` | Pelias, self-hosted Nominatim | Place search disabled; clicks and coordinates still work |
| `RouteSourceConnector` | ArcGIS, WFS, GeoJSON, Shapefile, GPX, KML, CSV, CKAN, REST | Reported as a stated gap on the Coverage screen |

Every seam has a **disabled state that is distinct from a broken one**. This is
not a nicety: `/health/components` separates them because "elevation is disabled
because no provider is set" is a deployment choice while "elevation is
unavailable" is an incident, and collapsing the two wakes someone at 3am for a
working system.

## Three load-bearing decisions

### 1. Constraint enforcement wraps the engine, it does not live inside it

Valhalla's bicycle costing cannot express several of the constraints Contour must
honour. There is no maximum gradient — `use_hills` is a 0–1 *preference* whose own
documentation warns it "is not always possible" to satisfy. There is no
unpaved-distance budget, no unknown-surface budget, and no concept of unknown data
at all.

So routing is a loop:

```
solve → attribute → validate → exclude violations → solve again
```

The engine produces geometry; a deterministic validator scores every segment
against the constraint set; violating segments feed back as `exclude_locations`
on the next pass. The loop never edits geometry — it changes the *question*.

Three ways of running out are handled distinctly: attempts exhausted, exclusions
making the request unroutable, and the engine returning identical geometry twice.
The last halts immediately rather than spending the remaining budget re-asking a
question whose answer cannot change.

When no compliant route exists, the closest attempt is returned — closest measured
by how much route distance breaks the rules, not by which was tried last — with
the violating segments named and a `NOT_FEASIBLE` verdict. §7.6 forbids silently
dropping a requirement.

Keeping this outside the seam means swapping the engine changes how routes are
*found* and never changes what counts as compliant.

### 2. Unknown is a stored state, not a default

Every segment attribute is a value paired with a `KnowledgeStatus`:
`known`, `unknown`, `not_applicable` or `conflicting`. Reading a value without its
status is a bug.

Two consequences run through the whole system:

- **Constraints have three outcomes.** Satisfied, violated, and *could not be
  checked*. A route whose surface is unknown across 40% of its length has not been
  shown to meet "paved only". Where the unknown portion could not possibly breach
  a limit, compliance is provable and is reported as satisfied — the distinction
  is evidential, not pessimism.
- **Source values are never collapsed into an engine's generalisation.** Valhalla's
  `Surface` enum documents itself as a "generalized representation", so segments
  keep the verbatim source tag alongside it and only the verbatim value is ever
  shown as what the source said. Similarly `CycleLane::kNone` means "no specified
  bicycle lane" — nothing was specified — so it maps to `unknown` rather than
  manufacturing the negative fact that no lane exists.

### 3. A deployment limitation is not a per-route unknown

Contour has no hydrography source, so the "no unexplained water crossing" check
cannot run for any route at all. Treating that as missing data made
`FULLY_SATISFIED` unreachable: every route in the system reported "satisfied with
unknown data", and a warning that fires always is a warning nobody reads.

Checks therefore distinguish:

- `UNEVALUABLE` — a fact missing about *this route*. Downgrades the verdict.
- `NOT_AVAILABLE` — a capability *this deployment* lacks. Reported as a standing
  limitation alongside the result and on the Coverage screen, and does not
  downgrade the verdict.

## Data model

29 tables. The shapes that matter:

- **Geometries are separate columns.** `geom_original`, `geom_normalised`,
  `geom_matched`, `geom_generated`. A map-matched line never overwrites what a
  source or a user drew, so the difference can be shown when it is material.
- **Route versions are immutable.** An edit writes a new version pointing at its
  parent, which gives undo, redo, named versions, branching, comparison and
  restore one shared mechanism instead of six.
- **Hot attributes are denormalised onto `route_segment`**, because routing
  validation scans every segment of every candidate and an attribute-per-row join
  at that volume is too slow. The full set, with per-attribute provenance, lives
  in `segment_attribute`; the denormalised columns are a derived index over it,
  not a replacement.
- **Sources are versioned, never overwritten.** A changed source produces a new
  `route_source_version`. Where two sources disagree, both are kept and the
  disagreement is recorded in `source_discrepancy` with the version each result
  was computed from.
- **Scores store components, not just a total.** A total cannot explain a ranking.

## Long routes

Valhalla ships a 500 km per-request ceiling for bicycle costing and allows 50
locations. The Wild Atlantic Way is roughly 2,500 km, so no single request can
ever cover it, and raising the ceiling far enough to try would produce one
enormous all-or-nothing search.

Contour generates long routes as validated sections and stitches them into a
single final route. The configured limit is raised to 1,000 km to give each
section headroom rather than removed.

The same constraint rules out engine-provided alternatives for the reference
project: `alternates` is documented as unsupported on multipoint routes, and the
Wild Atlantic Way is routed with corridor anchors throughout. Contour's four
alternatives therefore come from solving the same journey under genuinely
different preference profiles, with near-copies discarded by shared distance
measured on **edge identity** rather than geometric proximity — two roads running
parallel are not the same road.

## Language models

The intent parser converts a request into structured constraints and records
which phrase produced each value. The default implementation is deterministic and
rule-based, needing no network and no key. That is the default rather than a
fallback: a route request is a small, regular language, and a rule-based reading
is reproducible, auditable and can show its working.

A model-backed parser sits behind the same interface for phrasing the rules do
not cover. Either way, the model may parse and explain. It never produces
geometry, access, surface, elevation, safety, ferry, traffic or service claims,
and routing reads only the structured intent — never the original text.

## Testing strategy

- **Unit** — the arithmetic and the rules, with no I/O.
- **Property** (Hypothesis) — invariants example tests miss, such as
  `ascent − descent == net elevation change` across random profiles.
- **Integration** — against live PostGIS and a live Valhalla. Tests needing
  routing tiles skip with the reason and the command that fixes it, rather than
  passing vacuously.
- **End to end** (Playwright) — desktop and mobile, with axe-core.

The bar for a test here is that it fails for one identifiable reason. Several
tests in this codebase exist because writing them found a real bug: the median
filter replacing good samples instead of the artefact, the climb trim erasing
climbs behind flat approaches, and a licence rule blocking an export that
CC-BY-NC plainly permits.
