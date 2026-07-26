#!/usr/bin/env bash
#
# Build Valhalla routing tiles from an OpenStreetMap extract.
#
# Contour's default extract is Ireland and Northern Ireland from Geofabrik,
# which covers the Wild Atlantic Way reference project. Any .osm.pbf works;
# set CONTOUR_OSM_EXTRACT_URL to change it.
#
# Requires network access to the extract host. Where egress policy blocks it,
# download the extract by another route and pass a local path instead:
#
#   ./infra/valhalla/build-tiles.sh /path/to/ireland-and-northern-ireland.osm.pbf
#
# The OSM extract and everything derived from it are ODbL licensed. The licence
# and its attribution requirement are recorded in docs/source_registry.md and
# enforced at export and publish time; see docs/licensing_and_attribution.md.

set -euo pipefail

CONFIG="${CONTOUR_VALHALLA_CONFIG:-/opt/contour/valhalla.json}"
TILE_DIR="${CONTOUR_VALHALLA_TILE_DIR:-/opt/contour/valhalla-tiles}"
EXTRACT_URL="${CONTOUR_OSM_EXTRACT_URL:-https://download.geofabrik.de/europe/ireland-and-northern-ireland-latest.osm.pbf}"
WORK_DIR="${CONTOUR_OSM_WORK_DIR:-/opt/contour/osm}"
LOCAL_EXTRACT="${1:-}"

log() { printf '[valhalla-tiles] %s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*"; }

[ -f "${CONFIG}" ] || {
  echo "config not found at ${CONFIG}; run infra/valhalla/make-config.sh first" >&2
  exit 1
}
command -v valhalla_build_tiles >/dev/null 2>&1 || {
  echo "valhalla_build_tiles not found; run infra/valhalla/build.sh first" >&2
  exit 1
}

mkdir -p "${WORK_DIR}" "${TILE_DIR}"

# ---------------------------------------------------------------------------
# 1. Obtain the extract
# ---------------------------------------------------------------------------
if [ -n "${LOCAL_EXTRACT}" ]; then
  [ -f "${LOCAL_EXTRACT}" ] || { echo "no such extract: ${LOCAL_EXTRACT}" >&2; exit 1; }
  EXTRACT="${LOCAL_EXTRACT}"
  log "using local extract ${EXTRACT}"
else
  EXTRACT="${WORK_DIR}/$(basename "${EXTRACT_URL}")"
  if [ -f "${EXTRACT}" ]; then
    log "reusing ${EXTRACT}"
  else
    log "downloading ${EXTRACT_URL}"
    # --fail so an error page is never mistaken for an extract; the .part
    # rename means an interrupted download is not reused as if complete.
    if ! curl --fail --location --show-error --silent \
         --output "${EXTRACT}.part" "${EXTRACT_URL}"; then
      echo "" >&2
      echo "Could not download ${EXTRACT_URL}." >&2
      echo "If this environment's egress policy blocks the host, fetch the" >&2
      echo "extract separately and re-run with its path as the first argument." >&2
      rm -f "${EXTRACT}.part"
      exit 1
    fi
    mv "${EXTRACT}.part" "${EXTRACT}"
  fi
fi

# Recorded so an import can be traced back to the exact input bytes (§6.7.6).
log "extract sha256: $(sha256sum "${EXTRACT}" | cut -d' ' -f1)"
log "extract size:   $(du -h "${EXTRACT}" | cut -f1)"

# ---------------------------------------------------------------------------
# 2. Build tiles
# ---------------------------------------------------------------------------
log "building tiles into ${TILE_DIR} (this takes a while)"
valhalla_build_tiles -c "${CONFIG}" "${EXTRACT}"

# A tar extract is what valhalla_service memory-maps in production; it is
# markedly faster to load than the individual tile files.
if command -v valhalla_build_extract >/dev/null 2>&1; then
  log "packaging tile extract"
  valhalla_build_extract -c "${CONFIG}" -v || log "tile extract packaging skipped"
fi

log "tile count: $(find "${TILE_DIR}" -name '*.gph' | wc -l)"
log "done — restart valhalla_service to pick the tiles up"
