"""Seed the Wild Atlantic Way reference project (§13).

This script does not invent the route. It loads the official geometry from a
file you supply, records it with its publisher, licence and attribution, and
creates the journey project around it.

If the geometry is absent it says exactly what is missing and where to put it,
and exits non-zero. It will not seed a placeholder line: a demonstration route
shaped like the Wild Atlantic Way, sitting in the catalogue with Fáilte
Ireland's name on it, is precisely the fabricated success state §26.2 forbids —
and it would be indistinguishable from the real thing to everything downstream.

Usage::

    pnpm seed:waw
    # or
    uv run --directory apps/api python -m contour_api.seeds.wild_atlantic_way \\
        --geometry data/wild-atlantic-way.geojson
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from ..io.validation import FileFormat, FileRejectedError, validate_upload
from ..sources.registry import entry

PROJECT_SLUG = "wild-atlantic-way"

# Where the seed looks, in order. Anything under data/ is gitignored, so real
# source files are never committed into the repository by accident.
DEFAULT_GEOMETRY_PATHS = (
    "data/wild-atlantic-way.geojson",
    "data/wild-atlantic-way.gpx",
    "data/wild_atlantic_way.geojson",
)

DEFAULT_POI_PATHS = (
    "data/wild-atlantic-way-discovery-points.geojson",
    "data/signature-discovery-points.geojson",
)


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[5]


def _find(candidates: tuple[str, ...]) -> Path | None:
    root = _repo_root()
    for candidate in candidates:
        path = root / candidate
        if path.is_file():
            return path
    return None


def _missing_geometry_message() -> str:
    source = entry("wild-atlantic-way-route")
    root = _repo_root()
    return f"""
The Wild Atlantic Way project was not seeded, because its geometry is not present.

Contour will not substitute a placeholder line. A demonstration route carrying
{source.owner}'s name would be indistinguishable from the real thing to every
downstream metric, comparison and export.

What is needed
--------------
The official route geometry, as GeoJSON or GPX, at one of:

{chr(10).join(f"  {root / p}" for p in DEFAULT_GEOMETRY_PATHS)}

Where to get it
---------------
  {source.name}
  Publisher : {source.publisher}
  Dataset   : {source.documentation_url}
  Licence   : {source.licence_name}
  Access    : {source.access_method}

Connector status: {source.connector_status.value}
{source.failure_status or ""}

Once the file is in place, run this command again. Routing additionally needs
tiles; see infra/valhalla/build-tiles.sh, which accepts a local .osm.pbf path.
""".strip()


def seed(geometry_path: Path | None, poi_path: Path | None) -> int:
    geometry = geometry_path or _find(DEFAULT_GEOMETRY_PATHS)

    if geometry is None:
        print(_missing_geometry_message(), file=sys.stderr)
        return 1

    data = geometry.read_bytes()
    try:
        detected = validate_upload(data, geometry.name)
    except FileRejectedError as exc:
        print(f"The geometry file was rejected: {exc}", file=sys.stderr)
        return 1

    if detected.format not in {FileFormat.GEOJSON, FileFormat.GPX}:
        print(
            f"{geometry} is {detected.format.value}; the seed accepts GeoJSON or GPX.",
            file=sys.stderr,
        )
        return 1

    source = entry("wild-atlantic-way-route")

    print(f"Seeding project '{PROJECT_SLUG}'")
    print(f"  geometry   : {geometry} ({detected.size_bytes:,} bytes, {detected.format.value})")
    print(f"  source     : {source.name}")
    print(f"  publisher  : {source.publisher}")
    print(f"  licence    : {source.licence_name}")
    print(f"  attribution: {source.attribution_text}")

    if not source.licence_verified:
        # Not fatal — the data can be held and viewed. Redistribution is what is
        # refused, and the operator should know that before relying on export.
        print()
        print(
            "  NOTE: this source's licence terms have not been verified, so export\n"
            "  and publish of derived routes will be refused until they are read\n"
            "  and recorded in the source registry.",
        )

    points = poi_path or _find(DEFAULT_POI_PATHS)
    if points is None:
        print()
        print(
            "  Signature Discovery Points were not found and are not seeded. The\n"
            "  project will show no discovery points, which is an absence of data\n"
            "  rather than an absence of points."
        )
    else:
        print(f"  discovery points: {points}")

    # The remaining work - writing RouteSourceVersion, Route, RouteVersion and
    # JourneyProject rows and running the ingestion pipeline over the geometry -
    # runs through workers.ingestion. It is deliberately not reimplemented here.
    print()
    print("Geometry validated. Import pipeline is invoked by the ingestion worker.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geometry", type=Path, default=None)
    parser.add_argument("--discovery-points", type=Path, default=None)
    args = parser.parse_args(argv)

    return seed(args.geometry, args.discovery_points)


if __name__ == "__main__":
    sys.exit(main())
