# Performance budgets

Budgets are stated first and measured second. Where a figure has not been
measured, this document says so rather than quoting the target as though it were
a result.

## Budgets

| Operation | Budget | Measured |
| --- | --- | --- |
| Initial map interaction | 2.0 s | Not measured — no basemap configured |
| Route search | 400 ms p95 | Not measured — catalogue is empty |
| Local route generation (< 50 km) | 1.5 s p95 | **Not measured — no routing tiles** |
| Long route generation (> 500 km) | 30 s p95, streamed by section | Not measured |
| Route drag recalculation | 500 ms p95 | Not measured |
| Elevation chart interaction | 16 ms per frame | Not measured |
| Map mode switch | 150 ms, camera preserved | Not measured |
| 3D terrain load | 3.0 s | Not measured |
| Import processing (10 MB GPX) | 5 s | Not measured |
| Export generation | 2 s | Not measured |

Every "not measured" is a consequence of the data gap described in the README,
not of the measurement being skipped. The budgets and the harness are in place;
the routes to measure against are not.

## What has been measured

| Item | Result |
| --- | --- |
| Valhalla build from source | ~21 minutes on 4 cores |
| API test suite (194 tests) | ~3 s |
| Web test suite (14 tests) | ~0.3 s |
| Alembic full migration round trip | ~2 s |
| `/health/components` including live Valhalla probe | < 100 ms |

## Method

Budgets are p95 against a mid-range device profile and a throttled connection,
measured in Playwright. Route generation budgets exclude the first request after
a tile rebuild, which pays a page-cache cost that is not representative.

Long route generation is streamed by validated section rather than returned as
one response, so the budget is time-to-first-section as well as total.
`stages_completed` / `stages_total` on the job record report genuine progress by
completed processing stage — never a synthetic percentage.
