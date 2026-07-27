"""OpenStreetMap tag interpretation (§6.3, §6.4).

The documented mapping from source tags into Contour's data model. Two rules
govern every function here, and between them they are most of what makes
Contour's figures defensible:

**A missing tag is unknown, never a default.** The overwhelming majority of
rural Irish lanes carry no ``surface`` tag at all. Reading that absence as
"paved" — which is what a plain ``tags.get("surface", "paved")`` does, and what
most tooling effectively does — manufactures a fact about tens of thousands of
kilometres of road that nobody has surveyed.

**A tag that says "no" is not the same as no tag.** ``cycleway=no`` is a
surveyor stating there is no cycle lane. An absent ``cycleway`` is nobody having
looked. Contour keeps these apart end to end, because a rider planning around
infrastructure needs to know which one they are dealing with.

Values are preserved verbatim alongside any interpretation. Where Contour maps
``surface=sett`` into the ``PAVED`` family for a distance metric, the original
``sett`` remains on the segment and is what gets shown to a person.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..models.enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)

# Values of `highway` Contour treats as part of the cycling network. A way with
# a highway tag outside this set is not imported as routable network.
ROUTABLE_HIGHWAY: dict[str, RoadClass] = {
    "motorway": RoadClass.MOTORWAY,
    "motorway_link": RoadClass.MOTORWAY,
    "trunk": RoadClass.TRUNK,
    "trunk_link": RoadClass.TRUNK,
    "primary": RoadClass.PRIMARY,
    "primary_link": RoadClass.PRIMARY,
    "secondary": RoadClass.SECONDARY,
    "secondary_link": RoadClass.SECONDARY,
    "tertiary": RoadClass.TERTIARY,
    "tertiary_link": RoadClass.TERTIARY,
    "unclassified": RoadClass.UNCLASSIFIED,
    "residential": RoadClass.RESIDENTIAL,
    "living_street": RoadClass.LIVING_STREET,
    "service": RoadClass.SERVICE,
    "track": RoadClass.TRACK,
    "path": RoadClass.PATH,
    "cycleway": RoadClass.CYCLEWAY,
    "footway": RoadClass.FOOTWAY,
    "bridleway": RoadClass.BRIDLEWAY,
    "steps": RoadClass.STEPS,
}

# surface=* values, grouped for distance metrics only. The raw value is always
# retained; this grouping exists so "how much of this route is unpaved" can be
# answered, not to replace what the surveyor wrote.
PAVED_SURFACES = frozenset(
    {
        "paved",
        "asphalt",
        "chipseal",
        "concrete",
        "concrete:lanes",
        "concrete:plates",
        "paving_stones",
        "sett",
        "cobblestone",
        "unhewn_cobblestone",
        "metal",
        "wood",
        "bricks",
        "paving_stones:lanes",
    }
)

UNPAVED_SURFACES = frozenset(
    {
        "unpaved",
        "compacted",
        "fine_gravel",
        "gravel",
        "shells",
        "rock",
        "pebblestone",
        "ground",
        "dirt",
        "earth",
        "grass",
        "grass_paver",
        "mud",
        "sand",
        "woodchips",
        "snow",
        "ice",
        "salt",
    }
)

# access=* values that permit passage, and those that forbid it.
_ACCESS_VALUES: dict[str, BicycleAccess] = {
    "yes": BicycleAccess.YES,
    "designated": BicycleAccess.DESIGNATED,
    "permissive": BicycleAccess.PERMISSIVE,
    "destination": BicycleAccess.DESTINATION,
    "customers": BicycleAccess.CUSTOMERS,
    "private": BicycleAccess.PRIVATE,
    "no": BicycleAccess.NO,
    "dismount": BicycleAccess.DISMOUNT,
    "official": BicycleAccess.DESIGNATED,
}

# Explicit statements that a cycle facility is absent.
_ABSENT_VALUES = frozenset({"no", "none"})


@dataclass(frozen=True, slots=True)
class Interpreted:
    """One attribute: what the source said, what Contour made of it, and status."""

    raw: str | None
    status: KnowledgeStatus
    value: object | None = None

    @property
    def known(self) -> bool:
        return self.status is KnowledgeStatus.KNOWN


UNKNOWN = Interpreted(raw=None, status=KnowledgeStatus.UNKNOWN)


def interpret_surface(tags: dict[str, str]) -> tuple[Interpreted, SurfaceFamily]:
    """Read ``surface``, falling back to ``tracktype`` only as evidence, not fact.

    ``tracktype`` grades a track from ``grade1`` (solid, often sealed) to
    ``grade5`` (soft earth). It is a firmness scale, not a surface, so Contour
    uses it to place a track in the unpaved family while leaving the *surface*
    itself unknown — the tag genuinely does not say what the track is made of.
    """
    raw = tags.get("surface")
    if raw:
        normalised = raw.strip().lower()
        if normalised in PAVED_SURFACES:
            return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN), SurfaceFamily.PAVED
        if normalised in UNPAVED_SURFACES:
            return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN), SurfaceFamily.UNPAVED
        # A surface value Contour does not recognise is still a surveyed fact.
        # It is kept verbatim and shown, but it cannot be counted in a paved or
        # unpaved total without inventing a classification.
        return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN), SurfaceFamily.UNKNOWN

    tracktype = tags.get("tracktype")
    if tracktype:
        return (
            Interpreted(raw=None, status=KnowledgeStatus.UNKNOWN),
            SurfaceFamily.UNPAVED if tracktype.lower() != "grade1" else SurfaceFamily.UNKNOWN,
        )

    return UNKNOWN, SurfaceFamily.UNKNOWN


def interpret_bicycle_access(tags: dict[str, str]) -> Interpreted:
    """Resolve bicycle access from the tag hierarchy.

    ``bicycle`` wins over ``vehicle``, which wins over ``access``, because the
    more specific tag is the one a mapper set deliberately. Where none is
    present the answer is unknown — *not* inferred from the highway class.

    The one exception is a positive statement Contour can make safely: a
    ``highway=cycleway`` is by definition for bicycles. That is the tag's
    meaning, not an inference from context.
    """
    for key in ("bicycle", "vehicle", "access"):
        raw = tags.get(key)
        if not raw:
            continue
        value = _ACCESS_VALUES.get(raw.strip().lower())
        if value is not None:
            return Interpreted(raw=f"{key}={raw}", status=KnowledgeStatus.KNOWN, value=value)
        # An unrecognised access value is recorded but not resolved: guessing
        # whether it permits passage is exactly the wrong call to make.
        return Interpreted(raw=f"{key}={raw}", status=KnowledgeStatus.UNKNOWN)

    if tags.get("highway") == "cycleway":
        return Interpreted(
            raw="highway=cycleway",
            status=KnowledgeStatus.KNOWN,
            value=BicycleAccess.DESIGNATED,
        )

    return UNKNOWN


def interpret_cycle_infrastructure(tags: dict[str, str]) -> Interpreted:
    """Read cycle infrastructure, keeping "absent" apart from "unrecorded".

    This is the distinction Valhalla's own vocabulary loses: its ``CycleLane``
    has a single ``kNone`` covering both. Contour reports ``ABSENT`` only where
    a mapper wrote ``cycleway=no``, and ``UNKNOWN`` where nobody wrote anything.
    """
    if tags.get("highway") == "cycleway":
        return Interpreted(
            raw="highway=cycleway",
            status=KnowledgeStatus.KNOWN,
            value=CycleLaneKind.SEPARATED,
        )

    for key in ("cycleway", "cycleway:both", "cycleway:left", "cycleway:right"):
        raw = tags.get(key)
        if not raw:
            continue
        value = raw.strip().lower()
        if value in _ABSENT_VALUES:
            return Interpreted(
                raw=f"{key}={raw}", status=KnowledgeStatus.KNOWN, value=CycleLaneKind.ABSENT
            )
        if value in {"track", "separate", "sidepath"}:
            return Interpreted(
                raw=f"{key}={raw}", status=KnowledgeStatus.KNOWN, value=CycleLaneKind.SEPARATED
            )
        if value in {"lane", "opposite_lane", "buffered_lane"}:
            return Interpreted(
                raw=f"{key}={raw}", status=KnowledgeStatus.KNOWN, value=CycleLaneKind.DEDICATED
            )
        if value in {"shared_lane", "share_busway", "shared", "opposite_share_busway"}:
            return Interpreted(
                raw=f"{key}={raw}", status=KnowledgeStatus.KNOWN, value=CycleLaneKind.SHARED
            )
        return Interpreted(raw=f"{key}={raw}", status=KnowledgeStatus.UNKNOWN)

    return UNKNOWN


def interpret_road_class(tags: dict[str, str]) -> Interpreted:
    raw = tags.get("highway")
    if not raw:
        return UNKNOWN
    value = ROUTABLE_HIGHWAY.get(raw.strip().lower())
    if value is None:
        return Interpreted(raw=raw, status=KnowledgeStatus.UNKNOWN)
    return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN, value=value)


def interpret_speed_limit(tags: dict[str, str]) -> Interpreted:
    """Parse ``maxspeed``, handling the units OSM actually carries.

    ``maxspeed=50`` is km/h by convention; ``maxspeed=30 mph`` is not. Ireland
    posts km/h but Northern Ireland posts mph, and the Wild Atlantic Way
    corridor runs into both, so the unit cannot be assumed from the country.
    """
    raw = tags.get("maxspeed")
    if not raw:
        return UNKNOWN

    text = raw.strip().lower()
    if text in {"none", "signals", "variable", "walk"}:
        # Real values, but not a number. Kept verbatim rather than dropped.
        return Interpreted(raw=raw, status=KnowledgeStatus.UNKNOWN)

    try:
        if text.endswith("mph"):
            return Interpreted(
                raw=raw,
                status=KnowledgeStatus.KNOWN,
                value=round(float(text.removesuffix("mph").strip()) * 1.609344),
            )
        if text.endswith("km/h") or text.endswith("kph"):
            text = text.removesuffix("km/h").removesuffix("kph").strip()
        return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN, value=round(float(text)))
    except ValueError:
        return Interpreted(raw=raw, status=KnowledgeStatus.UNKNOWN)


def interpret_boolean(tags: dict[str, str], key: str) -> Interpreted:
    """Read a yes/no tag, keeping "no" distinct from absent."""
    raw = tags.get(key)
    if raw is None:
        return UNKNOWN
    value = raw.strip().lower()
    if value in {"yes", "true", "1"}:
        return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN, value=True)
    if value in {"no", "false", "0"}:
        return Interpreted(raw=raw, status=KnowledgeStatus.KNOWN, value=False)
    # Values like lit=sunset-sunrise or bridge=viaduct are meaningful but are
    # not booleans; the raw value is kept and the boolean stays unknown.
    return Interpreted(raw=raw, status=KnowledgeStatus.UNKNOWN)


def is_ferry(tags: dict[str, str]) -> bool:
    return tags.get("route") == "ferry" or "ferry" in tags


def oneway_for_bicycles(tags: dict[str, str]) -> Interpreted:
    """Direction restriction as it applies to bicycles specifically.

    ``oneway:bicycle=no`` on a one-way street is extremely common and means
    contraflow cycling is permitted, so reading only ``oneway`` would tell a
    rider they cannot use a street they legally can.
    """
    raw = tags.get("oneway:bicycle")
    if raw is not None:
        return interpret_boolean({"v": raw}, "v")
    if tags.get("cycleway") in {"opposite", "opposite_lane", "opposite_track"}:
        return Interpreted(
            raw=f"cycleway={tags['cycleway']}", status=KnowledgeStatus.KNOWN, value=False
        )
    return interpret_boolean(tags, "oneway")


def is_routable(tags: dict[str, str]) -> bool:
    """Whether a way belongs in the cycling network at all."""
    if is_ferry(tags):
        return True
    highway = tags.get("highway")
    return bool(highway) and highway.strip().lower() in ROUTABLE_HIGHWAY


# Tags carried through verbatim so a segment can be explained from its source,
# even where Contour has no interpretation for them (§6.4).
PRESERVED_TAGS = (
    "highway",
    "surface",
    "smoothness",
    "tracktype",
    "bicycle",
    "access",
    "vehicle",
    "cycleway",
    "cycleway:left",
    "cycleway:right",
    "cycleway:both",
    "segregated",
    "oneway",
    "oneway:bicycle",
    "maxspeed",
    "lit",
    "width",
    "shoulder",
    "lanes",
    "bridge",
    "tunnel",
    "ford",
    "barrier",
    "construction",
    "seasonal",
    "incline",
    "mtb:scale",
    "sac_scale",
    "trail_visibility",
    "ref",
    "name",
    "route",
    "bicycle_road",
    "cyclestreet",
    "foot",
    "horse",
    "motor_vehicle",
    "conditional",
    "access:conditional",
    "bicycle:conditional",
)


def preserved(tags: dict[str, str]) -> dict[str, str]:
    """The subset of source tags stored verbatim on the segment."""
    return {key: tags[key] for key in PRESERVED_TAGS if key in tags}
