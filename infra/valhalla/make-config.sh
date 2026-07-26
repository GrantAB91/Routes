#!/usr/bin/env bash
#
# Generate Contour's Valhalla configuration.
#
# The config is produced by the installed `valhalla_build_config` rather than
# committed by hand, so it always matches the schema of the engine actually
# built. Contour's deviations from the defaults are applied afterwards and each
# one is justified below.
#
# Usage:
#   ./infra/valhalla/make-config.sh [output-path]

set -euo pipefail

OUTPUT="${1:-/opt/contour/valhalla.json}"
TILE_DIR="${CONTOUR_VALHALLA_TILE_DIR:-/opt/contour/valhalla-tiles}"
ELEVATION_DIR="${CONTOUR_ELEVATION_RASTER_DIR:-/opt/contour/dem}"

command -v valhalla_build_config >/dev/null 2>&1 || {
  echo "valhalla_build_config not found; run infra/valhalla/build.sh first" >&2
  exit 1
}

mkdir -p "$(dirname "${OUTPUT}")" "${TILE_DIR}" "${ELEVATION_DIR}"

valhalla_build_config \
  --mjolnir-tile-dir "${TILE_DIR}" \
  --mjolnir-tile-extract "${TILE_DIR}/tiles.tar" \
  --additional-data-elevation "${ELEVATION_DIR}" \
  > "${OUTPUT}.raw"

python3 - "${OUTPUT}.raw" "${OUTPUT}" <<'PY'
import json
import sys

raw_path, out_path = sys.argv[1], sys.argv[2]
with open(raw_path) as handle:
    config = json.load(handle)

limits = config.setdefault("service_limits", {}).setdefault("bicycle", {})

# Valhalla ships a 500 km per-request ceiling for bicycle costing. The Wild
# Atlantic Way is roughly 2,500 km, so no single request can ever cover it and
# raising the limit far enough to try would produce one enormous, slow,
# all-or-nothing search.
#
# Contour instead generates long routes as validated sections and stitches them
# into a single final route (§21.3). The limit is raised to 1,000 km to give
# each section generous headroom while keeping any one request bounded, rather
# than removed.
limits["max_distance"] = 1_000_000.0

# The default of 50 locations constrains how tightly a section can be pinned to
# a reference corridor. Contour shapes sections with corridor anchor points, so
# the ceiling is raised; sectioning still keeps the count per request modest.
limits["max_locations"] = 200

# Contour's solve loop feeds violating segments back as exclusions on each
# re-solve pass, and a long coastal section can accumulate more than the
# default allows before converging.
config["service_limits"]["max_exclude_locations"] = 200
config["service_limits"]["max_exclude_polygons_length"] = 10_000

with open(out_path, "w") as handle:
    json.dump(config, handle, indent=2)
    handle.write("\n")

print(f"wrote {out_path}")
print(f"  bicycle.max_distance  = {limits['max_distance']:.0f} m")
print(f"  bicycle.max_locations = {limits['max_locations']}")
PY

rm -f "${OUTPUT}.raw"
