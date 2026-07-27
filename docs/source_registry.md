# Source registry

<!--
  GENERATED FILE — do not edit by hand.
  Written by apps/api/src/contour_api/sources/generate_docs.py from the single
  definition in apps/api/src/contour_api/sources/registry.py, which also seeds
  the route_source table. Edit the registry, then regenerate.
-->

Every source Contour can draw on, what it permits, and what it does not
currently supply. §2.3 requires twenty fields per source; they are all below.

Two conventions matter when reading this file:

- **Connector status is not data coverage.** A source marked
  `implemented_blocked_egress` has a working, tested connector and has imported
  nothing, because this deployment's network policy refuses the host. That is a
  stated gap, never an implied absence of routes in that region.
- **An unverified licence blocks redistribution.** Where terms could not be read
  from the publisher, Contour refuses export and publish rather than assuming
  permission. The refusal message says the limitation is Contour's, not the
  publisher's.


## Summary

- 3 of 7 sources are currently importable.
- 4 source(s) contribute nothing; see below.
- 5 licence(s) are unverified and are therefore refused for export and publish.

## Gaps

| Source | Status | Why |
| --- | --- | --- |
| Wild Atlantic Way Route | `implemented_blocked_egress` | The host is refused by this deployment's network egress policy, so no data has been imported. The connector is implemented and tested against fixtures. |
| Wild Atlantic Way Signature Discovery Points | `implemented_blocked_egress` | The host is refused by this deployment's network egress policy, so no data has been imported. The connector is implemented and tested against fixtures. |
| EuroVelo 1 (Atlantic Coast Route) | `not_implemented` | Machine-readable access and licence terms need to be established with the ECF before this source can be imported. |
| TII National Cycle Network | `not_implemented` | Machine access and licence terms need confirming with TII. Until then this source contributes nothing and is shown as a gap. |

## Sources

### OpenStreetMap

`openstreetmap`

| Field | Value |
| --- | --- |
| 1. Source name | OpenStreetMap |
| 2. Owner / publisher | OpenStreetMap contributors / OpenStreetMap Foundation |
| 3. Documentation | https://www.openstreetmap.org/copyright |
|    Evidence for this entry | web search result summary; publisher page not directly reachable |
| 4. Access method | Planet dump streamed from the AWS Open Data mirror and filtered to Ireland in flight by osmium extract, so the 91 GB source is never stored (infra/valhalla/extract-from-planet.sh). Geofabrik's regional extract is the usual route and is refused by this deployment's egress policy. |
| 5. Authentication | none |
| 6. Licence | Open Database License 1.0 (`ODbL-1.0`) |
| 7. Required attribution | © OpenStreetMap contributors |
| 8. Permitted uses | Use, adaptation and redistribution, including commercially. |
| 9. Redistribution restrictions | Share-alike: a derived database must be offered under ODbL. Attribution to OpenStreetMap contributors is required. |
| 10. Geographic coverage | Global; Contour imports an Ireland and Northern Ireland extract. |
| 11. Route types | signed cycle routes, road network, paths, ferries |
| 12. Available attributes | bicycle access, surface, smoothness, tracktype, cycleway, network membership, maxspeed, highway class, lit, width, bridge, tunnel, ford, barrier, oneway, access conditions |
| 13. Update method | Full extract replacement; incremental updates supported upstream. |
| 14. Last source update | _not established_ |
| 15. Last successful import | — |
| 16. Last verification | web search result summary; publisher page not directly reachable |
| 17. Known quality limitations | Coverage of surface and access tags is uneven. Rural Irish lanes are frequently untagged, which Contour reports as unknown rather than filling in. Tag meaning varies by contributor and region. |
| 18. Connector status | `active` |
| 19. Failure status | _not established_ |
| 20. Contact / application requirement | _not established_ |

Supplies both the routing graph, via Valhalla tiles, and the segment attributes Contour validates against. The extract is clipped with osmium's `simple` strategy, which is single-pass and therefore the only one that works on a stream; ways crossing the bounding box lose their outside nodes and are dropped, so the box is padded into open sea and across the border rather than drawn at the coastline.

### Wild Atlantic Way Route

`wild-atlantic-way-route`

| Field | Value |
| --- | --- |
| 1. Source name | Wild Atlantic Way Route |
| 2. Owner / publisher | Fáilte Ireland / data.gov.ie (Open Data Unit) |
| 3. Documentation | https://data.gov.ie/dataset/wild-atlantic-way-route1 |
|    Evidence for this entry | web search result summary; publisher page not directly reachable |
| 4. Access method | ArcGIS Feature Service / dataset download via CKAN |
| 5. Authentication | none |
| 6. Licence | Creative Commons Attribution 4.0 (`CC-BY-4.0`) — **terms not verified** |
| 7. Required attribution | Contains data from Fáilte Ireland, via data.gov.ie |
| 8. Permitted uses | Reuse and redistribution with attribution, per the portal licence. |
| 9. Redistribution restrictions | Attribution to the originator is required. |
| 10. Geographic coverage | The Wild Atlantic Way touring route along the west coast of Ireland. |
| 11. Route types | official touring route |
| 12. Available attributes | geometry, route name, stage identifiers |
| 13. Update method | Manual re-import; the publisher does not document a change feed. |
| 14. Last source update | _not established_ |
| 15. Last successful import | never |
| 16. Last verification | web search result summary; publisher page not directly reachable |
| 17. Known quality limitations | The published line is a driving touring corridor. It is not a statement that the roads it follows are legal, suitable or safe for cycling, and Contour never treats it as one (§13.5). |
| 18. Connector status | `implemented_blocked_egress` |
| 19. Failure status | The host is refused by this deployment's network egress policy, so no data has been imported. The connector is implemented and tested against fixtures. |
| 20. Contact / application requirement | _not established_ |

### Wild Atlantic Way Signature Discovery Points

`wild-atlantic-way-discovery-points`

| Field | Value |
| --- | --- |
| 1. Source name | Wild Atlantic Way Signature Discovery Points |
| 2. Owner / publisher | Fáilte Ireland / data.gov.ie (Open Data Unit) |
| 3. Documentation | https://data.gov.ie/dataset/wild-atlantic-way1 |
|    Evidence for this entry | web search result summary; publisher page not directly reachable |
| 4. Access method | ArcGIS Feature Service |
| 5. Authentication | none |
| 6. Licence | Creative Commons Attribution 4.0 (`CC-BY-4.0`) — **terms not verified** |
| 7. Required attribution | Contains data from Fáilte Ireland, via data.gov.ie |
| 8. Permitted uses | Reuse and redistribution with attribution. |
| 9. Redistribution restrictions | Attribution to the originator is required. |
| 10. Geographic coverage | Signature Discovery Points along the Wild Atlantic Way. |
| 11. Route types | points of interest |
| 12. Available attributes | geometry, name, published description |
| 13. Update method | Manual re-import. |
| 14. Last source update | _not established_ |
| 15. Last successful import | never |
| 16. Last verification | web search result summary; publisher page not directly reachable |
| 17. Known quality limitations | Points mark visitor attractions. Their presence says nothing about services, water, food or shelter, and Contour does not present them as such. |
| 18. Connector status | `implemented_blocked_egress` |
| 19. Failure status | The host is refused by this deployment's network egress policy, so no data has been imported. The connector is implemented and tested against fixtures. |
| 20. Contact / application requirement | _not established_ |

### Copernicus DEM GLO-30

`copernicus-dem-glo-30`

| Field | Value |
| --- | --- |
| 1. Source name | Copernicus DEM GLO-30 |
| 2. Owner / publisher | European Space Agency / European Union / ESA |
| 3. Documentation | https://copernicus-dem-30m.s3.amazonaws.com/readme.html |
|    Evidence for this entry | publisher readme and tileList.txt read directly from the S3 bucket; tile geometry and resolution verified by opening the rasters |
| 4. Access method | Public S3 bucket of Cloud-Optimised GeoTIFF tiles |
| 5. Authentication | none for the public mirror |
| 6. Licence | Copernicus DEM licence (`copernicus-dem-eula`) — **terms not verified** |
| 7. Required attribution | © DLR e.V. 2010-2014, © Airbus Defence and Space GmbH |
| 8. Permitted uses | Not established in this environment. |
| 9. Redistribution restrictions | Not established. Contour therefore refuses to export or publish elevation values derived from this source until the terms are read from the publisher and recorded here. |
| 10. Geographic coverage | Global 30 m digital elevation model. |
| 11. Route types | _not established_ |
| 12. Available attributes | elevation |
| 13. Update method | Static release; replaced wholesale on a new release. |
| 14. Last source update | _not established_ |
| 15. Last successful import | — |
| 16. Last verification | publisher readme and tileList.txt read directly from the S3 bucket; tile geometry and resolution verified by opening the rasters |
| 17. Known quality limitations | Measured against the installed tiles rather than assumed. The grid is not a uniform 30 m: latitude spacing is one arc second (30.9 m) and longitude spacing is 1.5 arc seconds in the 50-60 degree band, so the limiting cell over Ireland is 30.9 m and Contour clamps sampling to it. This cannot resolve short steep ramps. It is also a surface model rather than a terrain model: vegetation and buildings read high, while sharp summits read LOW because a 30 m cell averages the peak away - Croagh Patrick reads 6 m below its published height and Mweelrea 45 m below. The second effect is the larger one and matters most when judging a col. See docs/elevation_method.md. |
| 18. Connector status | `active` |
| 19. Failure status | _not established_ |
| 20. Contact / application requirement | Tiles are imported and elevation is served, but the licence terms have still not been read from the publisher, so export and publish of derived elevation figures remain refused. Reading them is the single change that would unblock export. |

### EuroVelo 1 (Atlantic Coast Route)

`eurovelo-1`

| Field | Value |
| --- | --- |
| 1. Source name | EuroVelo 1 (Atlantic Coast Route) |
| 2. Owner / publisher | European Cyclists' Federation / European Cyclists' Federation |
| 3. Documentation | https://en.eurovelo.com/ev1 |
|    Evidence for this entry | not verified from the publisher in this environment |
| 4. Access method | Not established |
| 5. Authentication | unknown |
| 6. Licence | Not established (`unknown`) — **terms not verified** |
| 7. Required attribution | _not established_ |
| 8. Permitted uses | Not established. |
| 9. Redistribution restrictions | Not established; treated as restrictive. |
| 10. Geographic coverage | Atlantic coast of Europe, including the west of Ireland. |
| 11. Route types | international cycle route |
| 12. Available attributes | _not established_ |
| 13. Update method | Not established. |
| 14. Last source update | _not established_ |
| 15. Last successful import | never |
| 16. Last verification | not verified from the publisher in this environment |
| 17. Known quality limitations | Contour currently derives EuroVelo 1 membership from OpenStreetMap route relations rather than from the ECF directly. Those are two different sources and may disagree; where both are present the disagreement is recorded rather than resolved (§2.9). |
| 18. Connector status | `not_implemented` |
| 19. Failure status | _not established_ |
| 20. Contact / application requirement | Machine-readable access and licence terms need to be established with the ECF before this source can be imported. |

### TII National Cycle Network

`tii-national-cycle-network`

| Field | Value |
| --- | --- |
| 1. Source name | TII National Cycle Network |
| 2. Owner / publisher | Transport Infrastructure Ireland / Transport Infrastructure Ireland |
| 3. Documentation | _not established_ |
|    Evidence for this entry | not verified from the publisher in this environment |
| 4. Access method | Not established |
| 5. Authentication | unknown |
| 6. Licence | Not established (`unknown`) — **terms not verified** |
| 7. Required attribution | _not established_ |
| 8. Permitted uses | Not established. |
| 9. Redistribution restrictions | Not established; treated as restrictive. |
| 10. Geographic coverage | Republic of Ireland national cycle network. |
| 11. Route types | national cycle network |
| 12. Available attributes | _not established_ |
| 13. Update method | Not established. |
| 14. Last source update | _not established_ |
| 15. Last successful import | never |
| 16. Last verification | not verified from the publisher in this environment |
| 17. Known quality limitations | Unassessed; no data has been imported. |
| 18. Connector status | `not_implemented` |
| 19. Failure status | _not established_ |
| 20. Contact / application requirement | Machine access and licence terms need confirming with TII. Until then this source contributes nothing and is shown as a gap. |

### User imported files

`user-import`

| Field | Value |
| --- | --- |
| 1. Source name | User imported files |
| 2. Owner / publisher | The uploading user / n/a |
| 3. Documentation | _not established_ |
|    Evidence for this entry | format specifications implemented and tested in contour_api.io |
| 4. Access method | Direct upload (GPX, TCX, KML, GeoJSON, FIT) |
| 5. Authentication | user session |
| 6. Licence | Owned by the uploading user (`user-owned`) |
| 7. Required attribution | _not established_ |
| 8. Permitted uses | Private use by the uploading user; sharing is their decision. |
| 9. Redistribution restrictions | Contour makes no claim over uploaded files. Imported routes are private by default and are never published without an explicit act by their owner (§14.4). |
| 10. Geographic coverage | Wherever the user has been or planned. |
| 11. Route types | recorded tracks, planned routes |
| 12. Available attributes | geometry, elevation where recorded, timestamps |
| 13. Update method | Per upload. |
| 14. Last source update | _not established_ |
| 15. Last successful import | — |
| 16. Last verification | format specifications implemented and tested in contour_api.io |
| 17. Known quality limitations | Recorded tracks carry GNSS error and barometric drift. Contour keeps the original geometry and shows any map-matched version separately rather than replacing it (§6.8, §15.5). |
| 18. Connector status | `active` |
| 19. Failure status | _not established_ |
| 20. Contact / application requirement | _not established_ |
