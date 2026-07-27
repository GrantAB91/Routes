# Wild Atlantic Way acceptance

The reference project and the completion gate (§13, §22.8). This document
describes the flow, its current status, and exactly what is missing.

## Status: routing and elevation now run on real geography

Most of what this document previously listed as blocked is no longer blocked.
Two hosts turned out to be reachable, and between them they supply the road
network and the terrain:

| Input | State | How |
| --- | --- | --- |
| Ireland OSM extract | **Obtained** | Geofabrik is refused, but the AWS Open Data planet mirror is not. `infra/valhalla/extract-from-planet.sh` streams the 91 GB planet through `osmium extract` and writes only Ireland — 411 MB, never storing the source. |
| Routing tiles | **Built** | 283 Valhalla tiles from that extract. |
| Segment attributes | **Imported** | 1,468,207 ways, 265,207 km of network. 29.2% by distance carries a surface survey; the rest is unknown and stays unknown. |
| Digital elevation model | **Obtained** | 8 Copernicus GLO-30 tiles from the public S3 bucket. |
| WAW corridor geometry | **From OSM, not official** | See the caveat below. |
| Signature Discovery Points | Still blocked | `data.gov.ie` / `*.arcgis.com` refused. |
| Basemap tiles | Still blocked | Tile host refused. The route, its colouring and every measurement work over a blank canvas. |

### The corridor caveat

The Wild Atlantic Way is mapped in OpenStreetMap as 33 `type=route ref=WAW`
relations. `contour_api.ingestion.osm_corridor` extracts them: 5,258 ways, none
unresolved, **2,555.5 km** against a published length of about 2,500 km.

That is *OpenStreetMap's mapping of the route*, not Fáilte Ireland's published
geometry. The official publisher is still unreachable, so the two have never
been compared. Every artefact this produces carries that caveat in its own
properties, and nothing in Contour presents it as the official line (§2.7,
§26.2).

### What has actually been generated

Measured against the running stack, not against fixtures:

- Westport to Louisburgh, 22.2 km, 297 segments, 100% matched to the imported
  network, 226 m ascent, steepest 10.2% over a 100 m window.
- Sligo–Ballina–Westport–Leenane–Clifden–Galway, 287.2 km, 2,261 segments,
  100% matched, 3,088 m ascent, 100% elevation coverage, 52.8% paved and 47.1%
  unsurveyed. Correctly reported NOT_FEASIBLE against a 15% gradient limit,
  with the four offending segments named.
- Four alternative profiles produced two genuinely distinct routes; the other
  two were near-copies and were dropped rather than padding the list.

## The flow (§22.8)

"Runs" means run against the real Irish network, not against a fixture.

| # | Step | Implementation | Runs |
| --- | --- | --- | --- |
| 1 | Open the seeded project | `seeds/wild_atlantic_way.py` | Corridor extracted from OSM; the project seeder still expects a supplied file |
| 2 | Show official geometry and attribution | `RouteSourceVersion`, attribution manifest | ⚠️ OSM's mapping only — the official publisher is unreachable and the two are not the same claim |
| 3 | Show Signature Discovery Points | `PointOfInterest` | Blocked — `data.gov.ie` refused |
| 4 | Show cycling overlays | Segment attributes from OSM | ✅ 1.47 M ways imported and joined to routed geometry |
| 5 | Generate four distinct alternatives | `AlternativeGenerator`, four profiles | ✅ four profiles solved, two distinct routes returned, near-copies dropped |
| 6 | Compare every metric | `RouteView` composition, `/v1/routes/compare` | ✅ unknowns stay unknown; a route with no measured ascent is not ranked flattest |
| 7 | Set daily distance and ascent | `StageConstraints` | ✅ over HTTP |
| 8 | Generate stages | `plan_stages` | ✅ 150 km into three days, each with a reason |
| 9 | Inspect every climb | `climbs.detect` | ✅ tested; runs on measured DEM profiles |
| 10 | Edit one section | Waypoint editing, immutable versioning | ✅ map editing; version branching is data-model only |
| 11 | Verify metrics update | Recomputed per solve | ✅ every metric recomputed from the new geometry |
| 12 | Switch through all views | 12 map modes | ✅ modes render; basemap modes state the missing provider |
| 13 | 3D flyover | MapLibre terrain | Blocked — no terrain tile provider configured; stated in the UI |
| 14 | Topology view | `RouteTopology` | ✅ start, finish, ferries and surveyed surface changes |
| 15 | Export the route | GPX/GeoJSON writer, licence gate | ✅ over HTTP, with the manifest inside the file |
| 16 | Export one stage | Stage range export | ✅ tested |
| 17 | Reimport the export | GPX reader | ✅ round-trip, missing elevation preserved as missing |
| 18 | Verify geometry and metadata | Round-trip tests | ✅ tested |
| 19 | Show source disagreements | `SourceDiscrepancy` | Blocked — needs a second source for the same route |
| 20 | Complete on mobile | Responsive layout | ✅ layout; end-to-end mobile pass not run |

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
