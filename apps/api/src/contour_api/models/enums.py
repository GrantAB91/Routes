"""Shared vocabulary for route, segment and source attributes.

Two rules govern everything in this module.

**Unknown is a value, not an absence** (§2.6, §18.6). Every attribute carries a
:class:`KnowledgeStatus` alongside its value. Code may never read the value
without reading the status, and a missing attribute is never filled with zero,
false, "paved", "legal" or any other favourable default.

**Do not collapse distinct meanings** (§6.3). Where a routing engine
generalises a source value, Contour stores the source value verbatim *and* the
generalisation, and never presents the generalisation as what the source said.
The two cases that matter in practice are documented on
:class:`GeneralisedSurface` and :class:`CycleLaneKind` below.
"""

from __future__ import annotations

from enum import StrEnum


class KnowledgeStatus(StrEnum):
    """What Contour actually knows about one attribute of one segment.

    ``UNKNOWN`` and ``NOT_APPLICABLE`` are stored separately and never merged:
    "nobody has surveyed the surface here" and "surface is meaningless for a
    ferry crossing" support entirely different conclusions.

    ``CONFLICTING`` records that two sources disagree. Contour preserves both
    records and shows the disagreement rather than silently picking a winner
    (§2.9); the chosen value is recorded on the owning row.
    """

    KNOWN = "known"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"
    CONFLICTING = "conflicting"


class GeneralisedSurface(StrEnum):
    """Valhalla's generalised surface classes.

    Mirrors ``enum class Surface`` in ``valhalla/baldr/graphconstants.h`` at tag
    3.8.3, whose own comment describes it as a "Generalized representation of
    surface types". It is therefore *lossy*: OSM's ``surface=asphalt``,
    ``surface=concrete`` and ``surface=sett`` can all arrive here as ``PAVED``.

    Contour stores this value only to explain routing decisions made by the
    engine. Any statement to the user about what the surface *is* must come
    from :attr:`RouteSegment.surface_tag_raw`, the verbatim source value, with
    its own :class:`KnowledgeStatus`.
    """

    PAVED_SMOOTH = "paved_smooth"
    PAVED = "paved"
    PAVED_ROUGH = "paved_rough"
    COMPACTED = "compacted"
    DIRT = "dirt"
    GRAVEL = "gravel"
    PATH = "path"
    IMPASSABLE = "impassable"


class SurfaceFamily(StrEnum):
    """Coarse grouping used for route composition metrics and constraints.

    Constraints such as "maximum permitted unpaved distance" (§7.4) and
    "maximum permitted unknown surface distance" need a bucket, but the bucket
    must not invent knowledge. ``UNKNOWN`` is a first-class family and is
    reported as its own share of route distance everywhere composition is
    shown — it is never folded into ``PAVED`` or ``UNPAVED``.
    """

    PAVED = "paved"
    UNPAVED = "unpaved"
    UNKNOWN = "unknown"


class CycleLaneKind(StrEnum):
    """Cycle infrastructure alongside or forming a segment.

    Mirrors ``enum class CycleLane`` in ``valhalla/baldr/graphconstants.h`` at
    tag 3.8.3.

    Note the trap in the upstream vocabulary: ``kNone`` is documented as "No
    specified bicycle lane", which means *nothing was specified* — not that no
    cycle lane exists. Mapping it to "no infrastructure" would manufacture a
    negative fact from missing data. Contour therefore translates Valhalla's
    ``kNone`` to :attr:`KnowledgeStatus.UNKNOWN` unless the source explicitly
    tagged absence (e.g. OSM ``cycleway=no``), in which case :attr:`ABSENT` is
    recorded with :attr:`KnowledgeStatus.KNOWN`.
    """

    ABSENT = "absent"
    SHARED = "shared"
    DEDICATED = "dedicated"
    SEPARATED = "separated"


class BicycleAccess(StrEnum):
    """Legal bicycle access, as stated by a source.

    Contour never derives access from road class, from the presence of a cycle
    route relation, or from "it looks rideable" (§2.7). ``UNKNOWN`` propagates
    into the feasibility verdict instead of being resolved optimistically.
    """

    YES = "yes"
    NO = "no"
    DESIGNATED = "designated"
    PERMISSIVE = "permissive"
    DESTINATION = "destination"
    PRIVATE = "private"
    CUSTOMERS = "customers"
    DISMOUNT = "dismount"
    UNKNOWN = "unknown"


class RoadClass(StrEnum):
    """Road classification.

    Used as a *proxy* for traffic exposure only where labelled as such (§7.7).
    Contour holds no observed traffic data, so no traffic claim is derived from
    this field.
    """

    MOTORWAY = "motorway"
    TRUNK = "trunk"
    PRIMARY = "primary"
    SECONDARY = "secondary"
    TERTIARY = "tertiary"
    UNCLASSIFIED = "unclassified"
    RESIDENTIAL = "residential"
    SERVICE = "service"
    LIVING_STREET = "living_street"
    TRACK = "track"
    PATH = "path"
    CYCLEWAY = "cycleway"
    FOOTWAY = "footway"
    BRIDLEWAY = "bridleway"
    STEPS = "steps"
    FERRY = "ferry"
    UNKNOWN = "unknown"


class RouteOriginKind(StrEnum):
    """Where a catalogue route came from (§4.4).

    Shown on every route card and detail screen. ``GENERATED`` routes are those
    Contour produced itself and are never presented as an official route.
    """

    OFFICIAL = "official"
    COMMUNITY = "community"
    IMPORTED = "imported"
    PRIVATE = "private"
    GENERATED = "generated"


class GeometryKind(StrEnum):
    """Which geometry of a route or segment is being referred to (§18.3).

    These are stored in separate columns and never overwrite one another. In
    particular a map-matched geometry never replaces the original (§6.8); both
    are retained so the difference can be shown when material (§6.9).
    """

    ORIGINAL = "original"
    NORMALISED = "normalised"
    MATCHED = "matched"
    GENERATED = "generated"


class FeasibilityVerdict(StrEnum):
    """Result of evaluating a generated route against its constraints (§7.11).

    ``SATISFIED_WITH_UNKNOWN_DATA`` is deliberately distinct from
    ``FULLY_SATISFIED``: a route that appears compliant only because the data
    needed to check it is missing has not been shown to comply.
    """

    FULLY_SATISFIED = "fully_satisfied"
    SATISFIED_WITH_UNKNOWN_DATA = "satisfied_with_unknown_data"
    PARTIALLY_SATISFIED = "partially_satisfied"
    NOT_FEASIBLE = "not_feasible"


class ConnectorStatus(StrEnum):
    """Operational state of a source connector, shown on the Coverage screen.

    ``IMPLEMENTED_BLOCKED_EGRESS`` and ``AWAITING_CREDENTIALS`` exist so that an
    unreachable source is reported as an explicit gap with its exact
    requirement, never as an absence of data in that region (§4.2, §25.18).
    """

    ACTIVE = "active"
    DEGRADED = "degraded"
    FAILING = "failing"
    STALE = "stale"
    AWAITING_CREDENTIALS = "awaiting_credentials"
    AWAITING_APPROVAL = "awaiting_approval"
    IMPLEMENTED_BLOCKED_EGRESS = "implemented_blocked_egress"
    NOT_IMPLEMENTED = "not_implemented"
    DISABLED = "disabled"


class RedistributionPermission(StrEnum):
    """What a source licence permits Contour to do with derived data (§20.8-20.9).

    Enforced before publish and before export: where redistribution is not
    permitted, the action is blocked and the precise restriction is explained
    rather than the data being quietly omitted.
    """

    PERMITTED = "permitted"
    PERMITTED_WITH_ATTRIBUTION = "permitted_with_attribution"
    PERMITTED_SHARE_ALIKE = "permitted_share_alike"
    NON_COMMERCIAL_ONLY = "non_commercial_only"
    PROHIBITED = "prohibited"
    UNKNOWN = "unknown"


class BicycleType(StrEnum):
    """Bicycle profile (§7.2).

    Mirrors the ``bicycle_type`` values documented for Valhalla's ``bicycle``
    costing so the profile maps onto the engine without reinterpretation.
    ``EBIKE`` is a Contour-level profile layered on the hybrid costing: it
    adjusts gradient preference only, and makes no claim about battery range,
    which Contour has no data for (§7.2.6).
    """

    ROAD = "road"
    HYBRID = "hybrid"
    CROSS = "cross"
    MOUNTAIN = "mountain"
    EBIKE = "ebike"
    CUSTOM = "custom"


class StageBalanceStrategy(StrEnum):
    """Basis for rebalancing stages of a multi-day journey (§12.6)."""

    DISTANCE = "distance"
    ASCENT = "ascent"
    TIME = "time"
    WEIGHTED = "weighted"


class RouteVisibility(StrEnum):
    """Sharing state. New user routes are ``PRIVATE`` (§14.4, §20.1)."""

    PRIVATE = "private"
    UNLISTED = "unlisted"
    PUBLIC = "public"
    ARCHIVED = "archived"
