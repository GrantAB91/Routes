# Source ingestion

## Connectors

`RouteSourceConnector` implementations for ArcGIS Feature Service, WFS, GeoJSON,
Shapefile, GPX, KML, CSV with coordinates, CKAN portals, and documented REST
APIs. Each is selected by the `connector_key` on its registry entry.

**Current state: only `file_upload` is active.** Every other connector is
implemented and reported as `implemented_blocked_egress` or `not_implemented`
with its exact requirement. See [source_registry.md](source_registry.md).

## Guarantees

Every import is:

- **Idempotent** — keyed by `idempotency_key`; re-running a completed job returns
  the existing record rather than duplicating data.
- **Versioned** — a changed source produces a new `route_source_version`. Nothing
  is overwritten.
- **Retryable** — failures record an error code and detail, and are safe to run
  again.
- **Observable** — genuine stage-based progress (`stages_completed` /
  `stages_total`), never a synthetic percentage.

## What is stored per import

The original response or file where the licence permits retaining it, the
original geometry, the normalised geometry, any matched geometry, the source
identifier, a checksum, a transformation log, the job id, the import time, a
licence snapshot, the attribution text and the validation result.

The **licence snapshot** matters: an export decision made last month must remain
explainable against the licence that was in force then, not the one published
today.

## Snapping

Map matching never overwrites the original. Matched geometry is a separate
column, and the maximum and mean deviation are recorded so the difference can be
surfaced when it is material.

## Duplicate detection

Routes are grouped by source identifier, name, spatial overlap, direction and
geometry similarity. Grouping never discards a source version — both remain, and
where they disagree the difference is recorded in `source_discrepancy` with the
version each downstream result was computed from.

## Freshness

Each source carries a `staleness_threshold_days`. Passing it marks the source
stale on the Coverage screen and raises an operator alert. A stale source is
still shown with its age rather than hidden.
