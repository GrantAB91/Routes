#!/usr/bin/env bash
#
# Extract a regional OSM file by streaming the planet, without ever storing it.
#
# The usual way to get a regional extract is to download one from Geofabrik or
# BBBike. Where those hosts are unreachable but the AWS Open Data mirror of the
# OSM planet is not, this script gets the same result a different way: it pipes
# the planet through `osmium extract`, which filters on the fly and writes only
# the region. The planet is never written to disk, so a 91 GB source produces a
# few hundred megabytes of output on a host with far less free space than that.
#
# It is bandwidth-bound rather than disk-bound. At the ~67 MB/s measured from
# the AWS mirror the whole planet passes through in roughly 25 minutes.
#
# Usage:
#   ./infra/valhalla/extract-from-planet.sh                      # Ireland
#   ./infra/valhalla/extract-from-planet.sh -11,51.3,-5.3,55.5 ireland.osm.pbf
#
# Environment:
#   CONTOUR_PLANET_URL   planet .osm.pbf to stream (default: AWS Open Data)
#   CONTOUR_OSM_WORK_DIR output directory (default: /opt/contour/osm)

set -euo pipefail

# Ireland and Northern Ireland, padded into the sea on the Atlantic side so the
# Wild Atlantic Way corridor is nowhere near a clipped edge.
BBOX="${1:--11.0,51.3,-5.3,55.5}"
OUTPUT_NAME="${2:-ireland.osm.pbf}"
WORK_DIR="${CONTOUR_OSM_WORK_DIR:-/opt/contour/osm}"
PLANET_URL="${CONTOUR_PLANET_URL:-https://osm-pds.s3.amazonaws.com/2026/planet-260126.osm.pbf}"
OUTPUT="${WORK_DIR}/${OUTPUT_NAME}"

log() { printf '[extract] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

command -v osmium >/dev/null 2>&1 || {
  echo "osmium not found. It is installed by infra/valhalla/build.sh." >&2
  exit 1
}

mkdir -p "${WORK_DIR}"

log "planet : ${PLANET_URL}"
log "bbox   : ${BBOX}"
log "output : ${OUTPUT}"
log "streaming; the planet is filtered in flight and never stored"

# The `simple` strategy is single-pass, which is what makes this work on a
# stream at all. `complete_ways`, the default, needs to revisit the input to
# pull in nodes referenced from outside the box, and a pipe cannot be rewound.
#
# The cost is that ways crossing the boundary lose their outside nodes and are
# dropped. That is why the bbox above is padded well past the coastline: the
# clipped edge lands in open sea and across the border, not on any route.
#
# pipefail is set, so a truncated download fails the whole pipeline rather than
# silently yielding a partial extract that looks like a small country.
curl -sfL "${PLANET_URL}" \
  | osmium extract --bbox="${BBOX}" --strategy=simple -F pbf \
      --overwrite -o "${OUTPUT}" -

log "extract complete: $(du -h "${OUTPUT}" | cut -f1)"
log "sha256: $(sha256sum "${OUTPUT}" | cut -d' ' -f1)"
osmium fileinfo "${OUTPUT}" 2>/dev/null | grep -E 'Bounding box|Number of' || true

log "next: ./infra/valhalla/build-tiles.sh ${OUTPUT}"
