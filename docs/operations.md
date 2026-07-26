# Operations

## Services

```bash
./infra/scripts/dev-up.sh    # PostGIS, Redis, Valhalla; reports real state
```

The script is idempotent and prints what is actually available, including saying
plainly when no routing tiles exist. `GET /health/components` returns the same
as JSON.

Valhalla is started with `setsid` so it outlives the shell that launched it —
without that it dies with its parent and later health checks report it missing
for no visible reason.

## Database

```bash
pnpm db:upgrade                                    # apply migrations
uv run --directory apps/api alembic downgrade -1   # roll back one
uv run --directory apps/api alembic check          # detect model/schema drift
```

Migrations are verified by round trip in CI: upgrade, downgrade to base,
re-upgrade. Two things make that work and are easy to lose:

- the circular `route` / `route_version` foreign key is created explicitly,
  because autogenerate omits `use_alter` constraints;
- PostgreSQL ENUM types are dropped in `downgrade`, because they survive
  `drop_table` and break the next upgrade with "type already exists".

### Backup and restore

```bash
pg_dump --format=custom --file=contour-$(date -u +%Y%m%dT%H%M%SZ).dump contour
pg_restore --clean --if-exists --dbname=contour contour-TIMESTAMP.dump
```

Object storage holds original uploads and source archives and must be backed up
alongside the database — a restored database referencing missing originals loses
the ability to re-derive geometry.

## Routing tiles

```bash
./infra/valhalla/build-tiles.sh [/path/to/extract.osm.pbf]
```

Tiles are derived data and are not backed up; they are rebuilt from the extract.
The extract's SHA-256 is logged at build time so a tile set can be traced to the
exact input bytes.

Restart `valhalla_service` after a rebuild.

## Jobs

Import and export jobs record status, stage, counts, error code and detail.
Failed jobs are safe to retry by idempotency key. Dead jobs are inspected
through the operator dashboard.

## Reimporting a source

```bash
uv run --directory apps/api python -m contour_api.sources.generate_docs  # after registry edits
```

A reimport creates a new `route_source_version` rather than mutating the
existing one, so the previous version stays available and any discrepancy
between them is recorded.

## Alerts

| Condition | Meaning |
| --- | --- |
| Component `unavailable` | Configured but not answering — an incident |
| Component `disabled` | Not configured — a deployment choice, not an incident |
| Routing `degraded` | Engine running with no tiles; no route can be generated |
| Source past `staleness_threshold_days` | Data is older than intended |
| Connector failure | Recorded with `failure_status` and shown on Coverage |
