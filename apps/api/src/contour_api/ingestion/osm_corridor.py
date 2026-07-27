"""Extract a named route corridor from OpenStreetMap relations.

The Wild Atlantic Way is mapped in OSM as a set of ``type=route`` relations
carrying ``ref=WAW``, split by county. That is a real, licensed, attributable
source for where the route runs, and this module reads it.

It is emphatically **not** the official geometry. Fáilte Ireland publishes the
route, and this deployment cannot reach that publisher; what OSM contributors
have mapped may differ from it in ways nobody here can check. Everything this
module produces is therefore labelled as OpenStreetMap's mapping, attributed
under ODbL, and carries the caveat with it — a corridor presented as official
when it was derived from a different source is exactly the substitution §2.7
and §26.2 forbid, and it would be indistinguishable downstream from the real
thing.

Membership is a fact about tagging, not a claim about suitability. A way being
in the Wild Atlantic Way relation says the route passes along it; it says
nothing about whether that way is legal, safe or pleasant to cycle. Those come
from the way's own attributes, which the network import records separately.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..routing.validation import haversine_m
from .osm_import import _Point, _require_osmium

logger = logging.getLogger(__name__)

#: The caveat that travels with every corridor this module produces. Carried as
#: data rather than left to each caller to remember, because a caller that
#: forgets it publishes an unattributed claim about someone else's route.
OSM_DERIVED_CAVEAT = (
    "This corridor is the Wild Atlantic Way as mapped by OpenStreetMap "
    "contributors, not the official geometry published by Fáilte Ireland. "
    "Contour could not reach the official publisher from this deployment, so "
    "the two have never been compared and may differ."
)


@dataclass(frozen=True, slots=True)
class CorridorSegment:
    """One way of a named route, with the relation that placed it there."""

    osm_way_id: int
    relation_id: int
    relation_name: str | None
    coordinates: tuple[tuple[float, float], ...]

    @property
    def length_m(self) -> float:
        return sum(
            haversine_m(_Point(lat=a[1], lon=a[0]), _Point(lat=b[1], lon=b[0]))
            for a, b in zip(self.coordinates, self.coordinates[1:], strict=False)
        )


@dataclass
class Corridor:
    """A named route as OSM has it, with what could and could not be resolved."""

    ref: str
    name: str
    segments: list[CorridorSegment] = field(default_factory=list)
    relation_ids: list[int] = field(default_factory=list)
    #: Ways the relations name that are not in this extract. Counted rather
    #: than ignored: a corridor missing a third of its ways is a different
    #: shape, and the caller has to be able to see that.
    unresolved_way_count: int = 0
    caveat: str = OSM_DERIVED_CAVEAT

    @property
    def length_m(self) -> float:
        return sum(segment.length_m for segment in self.segments)

    @property
    def resolved_ratio(self) -> float | None:
        total = len(self.segments) + self.unresolved_way_count
        return len(self.segments) / total if total else None

    def as_dict(self) -> dict:
        return {
            "ref": self.ref,
            "name": self.name,
            "source": "openstreetmap",
            "licence": "ODbL-1.0",
            "attribution": "© OpenStreetMap contributors",
            "relation_ids": sorted(self.relation_ids),
            "way_count": len(self.segments),
            "unresolved_way_count": self.unresolved_way_count,
            "resolved_ratio": (
                round(self.resolved_ratio, 4) if self.resolved_ratio is not None else None
            ),
            "length_km": round(self.length_m / 1000, 1),
            "caveat": self.caveat,
        }

    def as_geojson(self) -> dict:
        """A MultiLineString, deliberately not stitched into one line.

        Joining the pieces would require deciding an order and a direction, and
        OSM relations are not reliably sorted. A guessed order produces a line
        that zig-zags across the country while looking authoritative; the
        unstitched geometry is honest about what is known.
        """
        return {
            "type": "Feature",
            "geometry": {
                "type": "MultiLineString",
                "coordinates": [
                    [[lon, lat] for lon, lat in segment.coordinates] for segment in self.segments
                ],
            },
            "properties": self.as_dict(),
        }


def extract_corridor(
    path,
    *,
    ref: str = "WAW",
    name: str = "Wild Atlantic Way",
    location_storage: str = "flex_mem",
) -> Corridor:
    """Read every way belonging to the relations carrying ``ref``.

    Two passes, for the same reason the network import needs two: relations name
    ways by id, and a way's coordinates are only available once nodes have been
    read.
    """
    osmium = _require_osmium()

    wanted: dict[int, tuple[int, str | None]] = {}
    relation_ids: set[int] = set()

    for relation in osmium.FileProcessor(str(path), osmium.osm.RELATION):
        tags = dict(relation.tags)
        if tags.get("type") != "route" or tags.get("ref") != ref:
            continue
        relation_ids.add(relation.id)
        for member in relation.members:
            if member.type == "w":
                # First relation wins. A way in two county relations is one
                # piece of road, and counting it twice would inflate the length.
                wanted.setdefault(member.ref, (relation.id, tags.get("name")))

    if not wanted:
        logger.warning("no relations with ref=%s found in %s", ref, path)
        return Corridor(ref=ref, name=name)

    corridor = Corridor(ref=ref, name=name, relation_ids=sorted(relation_ids))
    seen: set[int] = set()

    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations(location_storage)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
    )

    for way in processor:
        if way.id not in wanted:
            continue
        try:
            coordinates = tuple(
                (node.location.lon, node.location.lat)
                for node in way.nodes
                if node.location.valid()
            )
        except osmium.InvalidLocationError:
            continue
        if len(coordinates) < 2 or len(coordinates) != len(way.nodes):
            continue

        relation_id, relation_name = wanted[way.id]
        corridor.segments.append(
            CorridorSegment(
                osm_way_id=way.id,
                relation_id=relation_id,
                relation_name=relation_name,
                coordinates=coordinates,
            )
        )
        seen.add(way.id)

    corridor.unresolved_way_count = len(wanted) - len(seen)
    if corridor.unresolved_way_count:
        logger.info(
            "%d of %d %s ways were not in the extract",
            corridor.unresolved_way_count,
            len(wanted),
            ref,
        )
    return corridor


def main() -> int:
    """Write a corridor to GeoJSON.

        uv run python -m contour_api.ingestion.osm_corridor \
            /opt/contour/osm/ireland.osm.pbf --out data/wild-atlantic-way-osm.geojson

    The output carries its source, licence, attribution and caveat in its own
    properties, so a file that travels away from this command still says where
    it came from and what it is not.
    """
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="Extract a named route corridor from OSM.")
    parser.add_argument("path", type=Path, help="Path to an .osm.pbf extract")
    parser.add_argument("--ref", default="WAW", help="Route relation ref (default: WAW)")
    parser.add_argument("--name", default="Wild Atlantic Way")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    corridor = extract_corridor(args.path, ref=args.ref, name=args.name)

    if not corridor.segments:
        print(f"No relations with ref={args.ref} were found in {args.path}.")
        return 1

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(corridor.as_geojson()), encoding="utf-8")

    summary = corridor.as_dict()
    print(f"wrote {args.out}")
    print(f"  relations : {len(summary['relation_ids'])}")
    print(f"  ways      : {summary['way_count']} ({summary['unresolved_way_count']} unresolved)")
    print(f"  length    : {summary['length_km']} km")
    print("  source    : OpenStreetMap, ODbL-1.0")
    print(f"  caveat    : {summary['caveat']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
