"""A route as constraint evaluation and validation see it.

These are plain values, deliberately independent of SQLAlchemy and of any
routing engine. Validation is the part of Contour that decides whether a route
may be presented as meeting what a user asked for, so it must be testable
without a database, without tiles, and without a network.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field

from ..models.enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)
from ..providers.routing import LatLon


@dataclass(frozen=True, slots=True)
class SegmentView:
    """One attributed piece of a route.

    Each attribute is paired with its :class:`KnowledgeStatus`. Evaluation code
    must branch on the status before reading the value; a constraint that reads
    ``surface_family`` without checking ``surface_status`` will silently treat
    every unsurveyed lane as satisfying "paved only".
    """

    index: int
    start_distance_m: float
    distance_m: float

    surface_family: SurfaceFamily = SurfaceFamily.UNKNOWN
    surface_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    bicycle_access: BicycleAccess = BicycleAccess.UNKNOWN
    bicycle_access_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    road_class: RoadClass = RoadClass.UNKNOWN
    road_class_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    cycle_lane: CycleLaneKind | None = None
    cycle_lane_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN

    # Steepest gradient on this segment, over the analysis window recorded on
    # the owning route. None means no elevation coverage here.
    max_grade_percent: float | None = None
    elevation_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN

    is_ferry: bool | None = None
    oneway_bicycle: bool | None = None
    # True when the route traverses this segment against its allowed direction.
    traversed_against_oneway: bool = False

    coordinates: tuple[LatLon, ...] = ()
    graph_edge_id: str | None = None

    @property
    def end_distance_m(self) -> float:
        return self.start_distance_m + self.distance_m

    @property
    def midpoint(self) -> LatLon | None:
        if not self.coordinates:
            return None
        return self.coordinates[len(self.coordinates) // 2]


@dataclass(frozen=True, slots=True)
class RouteView:
    """A candidate route ready for validation."""

    segments: tuple[SegmentView, ...]
    origin: LatLon
    destination: LatLon
    # Distance in metres with no elevation coverage, from the elevation profile.
    elevation_gap_m: float = 0.0
    # Reference line the request was made against, when there is one — the
    # official Wild Atlantic Way corridor, for instance.
    corridor: tuple[LatLon, ...] = ()
    ferry_segments_declared: bool = True
    metadata: dict = field(default_factory=dict)

    @property
    def distance_m(self) -> float:
        return sum(segment.distance_m for segment in self.segments)

    def distance_where(self, predicate) -> float:
        return sum(s.distance_m for s in self.segments if predicate(s))

    def surface_composition_m(self) -> dict[str, float]:
        """Distance per surface family, always including an ``unknown`` key.

        Unknown is reported as its own share rather than folded into paved or
        unpaved, because a route that is 40% unsurveyed is a different
        proposition from one that is 40% gravel (§7.9.8).
        """
        composition = {family.value: 0.0 for family in SurfaceFamily}
        for segment in self.segments:
            if segment.surface_status is KnowledgeStatus.KNOWN:
                composition[segment.surface_family.value] += segment.distance_m
            else:
                composition[SurfaceFamily.UNKNOWN.value] += segment.distance_m
        return composition

    def road_class_composition_m(self) -> dict[str, float]:
        composition: dict[str, float] = {}
        for segment in self.segments:
            key = (
                segment.road_class.value
                if segment.road_class_status is KnowledgeStatus.KNOWN
                else RoadClass.UNKNOWN.value
            )
            composition[key] = composition.get(key, 0.0) + segment.distance_m
        return composition

    def cycle_infrastructure_composition_m(self) -> dict[str, float]:
        """Distance per cycle infrastructure kind.

        Segments whose infrastructure is unknown are counted as ``unknown``,
        never as ``absent``. Valhalla's own vocabulary conflates the two, which
        is exactly the distinction §2.6 requires be kept.
        """
        composition: dict[str, float] = {"unknown": 0.0}
        for segment in self.segments:
            if segment.cycle_lane_status is KnowledgeStatus.KNOWN and segment.cycle_lane:
                key = segment.cycle_lane.value
            else:
                key = "unknown"
            composition[key] = composition.get(key, 0.0) + segment.distance_m
        return composition


def shared_distance_m(left: Sequence[SegmentView], right: Sequence[SegmentView]) -> float:
    """Distance the two routes have in common, by graph edge identity.

    Comparing by edge identity rather than by geometry proximity avoids
    counting two roads that merely run parallel — a common pattern on coastal
    routes where an old road shadows a new one — as shared (§7.9.1).
    """
    right_edges = {s.graph_edge_id for s in right if s.graph_edge_id}
    if not right_edges:
        return 0.0
    return sum(s.distance_m for s in left if s.graph_edge_id and s.graph_edge_id in right_edges)
