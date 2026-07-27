"""Command line entry point for the OSM network import.

    uv run python -m contour_api.ingestion /opt/contour/osm/ireland.osm.pbf

Reports what it saw rather than a percentage bar: how many ways, how much
network, and — the figure that matters most — what share of it carries a surface
survey. A low share is a property of the source and is meant to be visible
(§19.5, §2.6).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

from ..db import dispose_engine, get_session_factory
from .osm_import import OsmiumUnavailableError, OsmNetworkImporter


async def _run(path: Path, *, source_slug: str, replace: bool) -> int:
    factory = get_session_factory()
    async with factory() as session:
        importer = OsmNetworkImporter(session=session)
        try:
            record = await importer.run(path, source_slug=source_slug, replace_existing=replace)
        except OsmiumUnavailableError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        except LookupError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    counters = importer.counters
    surveyed = (
        counters.length_m_surface_known / counters.length_m_total
        if counters.length_m_total
        else None
    )

    print(f"import      : {record.id} ({record.status})")
    print(f"ways seen   : {counters.ways_seen:,}")
    print(f"ways written: {counters.ways_written:,}")
    print(f"not routable: {counters.ways_rejected:,}")
    print(f"no geometry : {counters.ways_without_geometry:,}")
    print(f"ferry ways  : {counters.ferry_ways:,}")
    print(f"network     : {counters.length_m_total / 1000:,.0f} km")
    if surveyed is None:
        print("surface     : nothing imported, so no coverage figure")
    else:
        print(
            f"surface     : {surveyed * 100:.1f}% of the network by distance has a surface survey"
        )
        print("              The remainder is unknown, not paved. That is what the source says.")
    return 0 if record.status == "succeeded" else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="Import an OSM extract into network_way.")
    parser.add_argument("path", type=Path, help="Path to an .osm.pbf extract")
    parser.add_argument(
        "--source-slug",
        default="openstreetmap",
        help="Registry slug the import is attributed to (default: openstreetmap)",
    )
    parser.add_argument(
        "--keep-previous",
        action="store_true",
        help=(
            "Keep earlier imports' rows. Off by default: merging two vintages of "
            "OSM produces a network that never existed at any point in time."
        ),
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if not args.path.is_file():
        print(f"error: {args.path} does not exist", file=sys.stderr)
        return 2

    async def _main() -> int:
        try:
            return await _run(
                args.path, source_slug=args.source_slug, replace=not args.keep_previous
            )
        finally:
            await dispose_engine()

    return asyncio.run(_main())


if __name__ == "__main__":
    raise SystemExit(main())
