# Wild Atlantic Way acceptance

The reference project and the completion gate (§13, §22.8). This document
describes the flow, its current status, and exactly what is missing.

## Status: blocked on data, not code

Every step below has its implementation in place and tested against fixtures.
None has run against the real Wild Atlantic Way, because three inputs cannot be
obtained in the reference environment:

| Input | Needed for | Blocked by |
| --- | --- | --- |
| Ireland OSM extract | Routing tiles, segment attributes | `download.geofabrik.de` refused by network policy |
| Official WAW route geometry | The reference corridor | `data.gov.ie` / `*.arcgis.com` refused |
| Signature Discovery Points | Points of interest | as above |
| Digital elevation model | Ascent, gradient, climbs | DEM host refused |
| Basemap tiles | Standard, topographic, satellite modes | Tile host refused |

**Both routes around this are supported and need no network:**

1. `./infra/valhalla/build-tiles.sh /path/to/ireland.osm.pbf` accepts a local
   extract.
2. GPX and GeoJSON import works today, so the official geometry can be loaded
   from a file placed in the repository.

## The flow (§22.8)

| # | Step | Implementation | Runs |
| --- | --- | --- | --- |
| 1 | Open the seeded project | `seeds/wild_atlantic_way.py` | Needs geometry |
| 2 | Show official geometry and attribution | `RouteSourceVersion`, attribution manifest | Needs geometry |
| 3 | Show Signature Discovery Points | `PointOfInterest` | Needs source |
| 4 | Show cycling overlays | Segment attributes from OSM | Needs extract |
| 5 | Generate four distinct alternatives | `AlternativeGenerator`, four profiles defined | Needs tiles |
| 6 | Compare every metric | `RouteView` composition, `RouteVariant` overlap | Needs tiles |
| 7 | Set daily distance and ascent | `StageConstraints` | ✅ tested |
| 8 | Generate stages | `plan_stages` | ✅ tested |
| 9 | Inspect every climb | `climbs.detect` | ✅ tested |
| 10 | Edit one section | Immutable versioning | Needs tiles |
| 11 | Verify metrics update | Recomputed per version | Needs tiles |
| 12 | Switch through all views | 12 map modes | ✅ modes render; basemap modes show the missing-provider state |
| 13 | 3D flyover | MapLibre terrain | Needs terrain tiles |
| 14 | Topology view | `RouteTopology` | ✅ renders |
| 15 | Export the route | GPX writer, attribution manifest | ✅ tested |
| 16 | Export one stage | Stage range export | ✅ tested |
| 17 | Reimport the export | GPX reader | ✅ tested |
| 18 | Verify geometry and metadata | Round-trip tests | ✅ tested |
| 19 | Show source disagreements | `SourceDiscrepancy` | Needs two sources |
| 20 | Complete on mobile | Responsive layout | ✅ layout; flow needs tiles |

## The corridor is not a cycling route

§13.5, and it is load-bearing. The published Wild Atlantic Way line is a driving
touring corridor. It is not a statement that the roads it follows are legal,
suitable or safe for cycling, and Contour never treats it as one.

It is stored as a **reference corridor**: alternatives are measured against it by
deviation, and the deviation inspector shows where a cycling route leaves it and
why. The registry entry carries this in its `known_quality_limitations` field,
and a test asserts the wording is still there.

## The four alternatives (§13.7)

Defined in `routing/solver.py::wild_atlantic_way_profiles`, each with the
plain-language reason shown on its route card:

1. **Closest to the official route** — stays as near the official line as legal
   cycling roads allow, accepting more climbing and more main road.
2. **Lowest ascent** — trades coastal proximity for flatter ground.
3. **Most cycle infrastructure** — favours verified infrastructure, which can
   mean longer and further from the coast.
4. **Balanced** — no single axis pushed hard.

They cannot come from Valhalla's `alternates` parameter, which is documented as
unsupported on multipoint routes, and the corridor is routed with anchor points
throughout. They come from solving the same journey under different preference
profiles, with near-copies discarded by shared distance on edge identity.

## Running it once the data is present

```bash
./infra/valhalla/build-tiles.sh /path/to/ireland-and-northern-ireland.osm.pbf
# place DEM tiles, set CONTOUR_ELEVATION_PROVIDER and CONTOUR_ELEVATION_DATASET_ID
pnpm seed:waw
pnpm test:acceptance:waw
```
