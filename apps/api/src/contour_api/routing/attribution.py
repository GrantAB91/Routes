"""Turning engine geometry into attributed segments, from Contour's own data.

This is the join that makes every constraint in §7.4 evaluable. Valhalla returns
a shape and, through ``/trace_attributes``, the OSM way identifier behind each
edge of it. Those identifiers are looked up in ``network_way`` — the table the
OSM import wrote — and what comes back is what a surveyor recorded.

The rule the whole module exists to enforce: **the engine's own attributes are
never used as attributes.** Valhalla reports a surface and a cycle lane for each
edge, and both are lossy re-encodings of the same OSM data (upstream calls its
``Surface`` a "Generalized representation", and its ``CycleLane::kNone`` means
"no specified bicycle lane", conflating surveyed-absent with never-surveyed).
Reading them would turn "nobody has surveyed this lane" into a definite claim
about its surface, across most of rural Ireland. So a way that is missing from
``network_way`` produces a segment whose every attribute is ``UNKNOWN``, and
that unknown share is reported rather than hidden (§2.6, §7.9.8).

Two things *are* read from the engine, because they are structural rather than
attributive: which edge the route used, and whether that edge was a ferry link
in the graph. Neither is a claim about the road; both are statements about the
path taken.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from itertools import pairwise

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)
from ..models.network import NetworkWay
from ..models.source import RouteSource, SourceImport
from ..providers.routing import CostingPreferences, LatLon, RouteCandidate
from ..providers.valhalla import TracedEdge, ValhallaProvider
from .model import RouteView, SegmentView
from .validation import haversine_m

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class WayAttributes:
    """The stored attributes for one OSM way, as a plain value.

    Separated from the ORM row so the assembly step can be tested without a
    database, and so a cache can hold it cheaply.
    """

    surface_family: SurfaceFamily = SurfaceFamily.UNKNOWN
    surface_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    bicycle_access: BicycleAccess = BicycleAccess.UNKNOWN
    bicycle_access_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    road_class: RoadClass = RoadClass.UNKNOWN
    road_class_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    cycle_lane: CycleLaneKind | None = None
    cycle_lane_status: KnowledgeStatus = KnowledgeStatus.UNKNOWN
    is_ferry: bool = False
    oneway_bicycle: bool | None = None

    @classmethod
    def from_row(cls, way: NetworkWay) -> WayAttributes:
        return cls(
            surface_family=way.surface_family,
            surface_status=way.surface_status,
            bicycle_access=way.bicycle_access,
            bicycle_access_status=way.bicycle_access_status,
            road_class=way.road_class,
            road_class_status=way.road_class_status,
            cycle_lane=way.cycle_lane,
            cycle_lane_status=way.cycle_lane_status,
            is_ferry=way.is_ferry,
            oneway_bicycle=way.oneway_bicycle,
        )


#: What a segment looks like when its way is not in the imported network. Every
#: attribute unknown — which is the truth, and which the surface composition and
#: the constraint outcomes both surface rather than absorb.
UNATTRIBUTED = WayAttributes()


@dataclass(frozen=True, slots=True)
class AttributionReport:
    """How much of the route could be attributed, and from what.

    Attached to the route's metadata so a reader can tell a genuinely
    unsurveyed road from a failure of Contour's own plumbing. Those look
    identical in a surface composition and are completely different problems.
    """

    edges_traced: int
    edges_matched: int
    distance_m: float
    distance_matched_m: float
    ways_missing: int

    @property
    def matched_share(self) -> float | None:
        if self.distance_m <= 0:
            return None
        return self.distance_matched_m / self.distance_m

    def as_dict(self) -> dict:
        return {
            "edges_traced": self.edges_traced,
            "edges_matched": self.edges_matched,
            "ways_missing_from_network": self.ways_missing,
            "distance_m": round(self.distance_m, 1),
            "distance_matched_m": round(self.distance_matched_m, 1),
            "matched_share": (
                round(self.matched_share, 4) if self.matched_share is not None else None
            ),
            "note": (
                "matched_share is how much of the route Contour could look up in "
                "its imported network. It is not a data-quality figure: a matched "
                "way whose surface was never surveyed still reports unknown."
            ),
        }


def _slice(coordinates: tuple[LatLon, ...], begin: int, end: int) -> tuple[LatLon, ...]:
    lo = max(0, min(begin, len(coordinates)))
    hi = max(lo, min(end + 1, len(coordinates)))
    return coordinates[lo:hi]


def _geometric_length_m(points: tuple[LatLon, ...]) -> float:
    return sum(haversine_m(a, b) for a, b in pairwise(points))


def segments_from_traced_edges(
    edges: list[TracedEdge],
    coordinates: tuple[LatLon, ...],
    attributes: dict[int, WayAttributes],
) -> tuple[SegmentView, ...]:
    """Assemble segments from traced edges and stored attributes.

    Distances are measured along the route's own shape rather than taken from
    the engine's per-edge ``length``. Trace runs in chunks, and an edge that
    straddles a chunk boundary is reported once per chunk; measuring geometry
    makes the parts sum to the whole regardless, and guarantees the segment
    distances add up to the route distance the user is shown.
    """
    segments: list[SegmentView] = []
    cursor = 0.0

    for index, edge in enumerate(edges):
        shape = _slice(coordinates, edge.begin_shape_index, edge.end_shape_index)
        distance_m = _geometric_length_m(shape) if len(shape) > 1 else edge.distance_m

        stored = attributes.get(edge.way_id) if edge.way_id is not None else None
        attrs = stored or UNATTRIBUTED

        # Ferry status has two independent witnesses. The engine's is structural
        # — it says the route crossed a ferry link — so it is trusted when the
        # way itself is not in the network. Where both speak, either saying yes
        # is enough: a missed ferry is a route that cannot be ridden.
        is_ferry: bool | None
        if stored is not None:
            is_ferry = stored.is_ferry or edge.is_ferry_by_engine
        elif edge.is_ferry_by_engine:
            is_ferry = True
        else:
            # No stored way and no ferry link: nothing observed either way.
            is_ferry = None

        segments.append(
            SegmentView(
                index=index,
                start_distance_m=cursor,
                distance_m=distance_m,
                surface_family=attrs.surface_family,
                surface_status=attrs.surface_status,
                bicycle_access=attrs.bicycle_access,
                bicycle_access_status=attrs.bicycle_access_status,
                road_class=attrs.road_class,
                road_class_status=attrs.road_class_status,
                cycle_lane=attrs.cycle_lane,
                cycle_lane_status=attrs.cycle_lane_status,
                is_ferry=is_ferry,
                oneway_bicycle=attrs.oneway_bicycle,
                coordinates=shape,
                graph_edge_id=edge.graph_edge_id,
            )
        )
        cursor += distance_m

    return tuple(segments)


@dataclass
class PostgisSegmentAttributor:
    """Attributes engine geometry from the imported OSM network in PostGIS.

    Two round trips per call: one to Valhalla for edge identity, one to PostGIS
    for the attributes of the ways involved. The way lookup is cached for the
    lifetime of the attributor, which is what makes the solve loop's repeated
    attempts cheap — successive attempts differ only where the exclusions bit.
    """

    session: AsyncSession
    provider: ValhallaProvider
    preferences: CostingPreferences = field(default_factory=CostingPreferences)
    #: Restrict the lookup to one import. A failed or partial import can leave
    #: rows behind, and a way answered from two vintages of OSM at once would
    #: produce a route attributed from a network that never existed on any day.
    import_id: uuid.UUID | None = None
    _cache: dict[int, WayAttributes | None] = field(default_factory=dict, repr=False)
    _import_resolved: bool = field(default=False, repr=False)

    async def attribute(self, candidate: RouteCandidate) -> RouteView:
        coordinates = candidate.coordinates
        if len(coordinates) < 2:
            raise ValueError("cannot attribute a route with no geometry")

        edges = await self.provider.trace_candidate(candidate, self.preferences)
        attributes = await self._attributes_for(
            {edge.way_id for edge in edges if edge.way_id is not None}
        )

        segments = segments_from_traced_edges(edges, coordinates, attributes)
        report = self._report(edges, segments, attributes)

        if report.matched_share is not None and report.matched_share < 0.5:
            # Loud, because at this level the likely cause is a stale or absent
            # OSM import rather than an unsurveyed region, and the difference
            # matters (§19.4).
            logger.warning(
                "only %.0f%% of the route matched the imported network; "
                "%d ways were not found. Is the OSM import current for this area?",
                report.matched_share * 100,
                report.ways_missing,
            )

        return RouteView(
            segments=segments,
            origin=coordinates[0],
            destination=coordinates[-1],
            metadata={
                "provider": candidate.provider,
                "engine_version": candidate.engine_version,
                "attribution": report.as_dict(),
            },
        )

    def _report(
        self,
        edges: list[TracedEdge],
        segments: tuple[SegmentView, ...],
        attributes: dict[int, WayAttributes],
    ) -> AttributionReport:
        matched_m = 0.0
        matched = 0
        missing: set[int] = set()

        for edge, segment in zip(edges, segments, strict=False):
            if edge.way_id is not None and edge.way_id in attributes:
                matched += 1
                matched_m += segment.distance_m
            elif edge.way_id is not None:
                missing.add(edge.way_id)

        return AttributionReport(
            edges_traced=len(edges),
            edges_matched=matched,
            distance_m=sum(s.distance_m for s in segments),
            distance_matched_m=matched_m,
            ways_missing=len(missing),
        )

    async def _resolve_import(self) -> None:
        """Pin to the most recent successful OSM import, once.

        Left unpinned only when there has never been a successful import, in
        which case there is nothing to disambiguate and every way will come back
        missing — which the attribution report states plainly.
        """
        if self._import_resolved or self.import_id is not None:
            self._import_resolved = True
            return
        self.import_id = await self.session.scalar(
            select(SourceImport.id)
            .join(RouteSource, RouteSource.id == SourceImport.source_id)
            .where(RouteSource.slug == "openstreetmap", SourceImport.status == "succeeded")
            .order_by(SourceImport.started_at.desc())
            .limit(1)
        )
        self._import_resolved = True

    async def _attributes_for(self, way_ids: set[int]) -> dict[int, WayAttributes]:
        """Look up ways, consulting and filling the per-instance cache.

        A negative result is cached too. Without that, a route through an area
        the import does not cover re-queries every missing way on every attempt
        of the solve loop.
        """
        await self._resolve_import()
        wanted = [way_id for way_id in way_ids if way_id not in self._cache]
        if wanted:
            # Chunked so a long route does not build an unbounded IN list.
            for batch in _chunks(wanted, 1000):
                query = select(NetworkWay).where(NetworkWay.osm_way_id.in_(batch))
                if self.import_id is not None:
                    query = query.where(NetworkWay.import_id == self.import_id)
                rows = (await self.session.scalars(query)).all()
                for row in rows:
                    self._cache[row.osm_way_id] = WayAttributes.from_row(row)
            for way_id in wanted:
                self._cache.setdefault(way_id, None)

        return {
            way_id: attrs for way_id in way_ids if (attrs := self._cache.get(way_id)) is not None
        }


def _chunks(items: list[int], size: int) -> list[list[int]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
