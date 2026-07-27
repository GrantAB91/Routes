"""Segment attribution from traced edges (§6.3, §7.9.8, §2.6).

The assembly step is tested here without a database or an engine. What it has
to get right is narrow and consequential: distances that add up, and a hard
refusal to let the routing engine's own generalisations stand in for a survey.
"""

from __future__ import annotations

from itertools import pairwise

import pytest

from contour_api.models.enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)
from contour_api.providers.routing import LatLon
from contour_api.providers.valhalla import (
    _TRACE_MAX_DISTANCE_M,
    TracedEdge,
    _chunk_shape,
)
from contour_api.routing.attribution import (
    AttributionReport,
    WayAttributes,
    segments_from_traced_edges,
)

# Four points along the R335 south of Louisburgh, ~1 km apart.
SHAPE = (
    LatLon(lat=53.7600, lon=-9.8100),
    LatLon(lat=53.7660, lon=-9.8000),
    LatLon(lat=53.7720, lon=-9.7900),
    LatLon(lat=53.7780, lon=-9.7800),
)


def edge(way_id: int | None, begin: int, end: int, **kwargs) -> TracedEdge:
    return TracedEdge(
        way_id=way_id,
        graph_edge_id=f"edge-{begin}",
        distance_m=0.0,
        begin_shape_index=begin,
        end_shape_index=end,
        **kwargs,
    )


SURVEYED = WayAttributes(
    surface_family=SurfaceFamily.PAVED,
    surface_status=KnowledgeStatus.KNOWN,
    bicycle_access=BicycleAccess.YES,
    bicycle_access_status=KnowledgeStatus.KNOWN,
    road_class=RoadClass.TERTIARY,
    road_class_status=KnowledgeStatus.KNOWN,
    cycle_lane=CycleLaneKind.ABSENT,
    cycle_lane_status=KnowledgeStatus.KNOWN,
)


class TestDistances:
    def test_segment_distances_tile_the_route_without_gaps(self) -> None:
        segments = segments_from_traced_edges(
            [edge(1, 0, 1), edge(2, 1, 2), edge(3, 2, 3)], SHAPE, {}
        )

        assert [s.index for s in segments] == [0, 1, 2]
        for previous, following in pairwise(segments):
            assert following.start_distance_m == pytest.approx(previous.end_distance_m)

    def test_total_distance_matches_the_shape(self) -> None:
        """Measured from geometry, so it cannot drift from what the user sees."""
        from contour_api.routing.attribution import _geometric_length_m

        segments = segments_from_traced_edges([edge(1, 0, 1), edge(2, 1, 3)], SHAPE, {})

        assert sum(s.distance_m for s in segments) == pytest.approx(_geometric_length_m(SHAPE))

    def test_an_edge_split_across_trace_chunks_is_not_double_counted(self) -> None:
        """The same way appearing twice must contribute its length once.

        Trace runs in windows and an edge straddling a boundary is reported by
        both. Because distance comes from the shape indices rather than from the
        engine's per-edge length, the two parts sum to the whole.
        """
        from contour_api.routing.attribution import _geometric_length_m

        whole = segments_from_traced_edges([edge(7, 0, 3)], SHAPE, {})
        split = segments_from_traced_edges([edge(7, 0, 1), edge(7, 1, 3)], SHAPE, {})

        assert sum(s.distance_m for s in split) == pytest.approx(sum(s.distance_m for s in whole))
        assert sum(s.distance_m for s in split) == pytest.approx(_geometric_length_m(SHAPE))

    def test_each_segment_carries_its_own_geometry(self) -> None:
        """The map needs to colour the right piece of road, not the whole line."""
        segments = segments_from_traced_edges([edge(1, 0, 1), edge(2, 1, 3)], SHAPE, {})

        assert segments[0].coordinates == SHAPE[0:2]
        assert segments[1].coordinates == SHAPE[1:4]


class TestNothingIsInvented:
    def test_a_way_missing_from_the_network_is_unknown_not_defaulted(self) -> None:
        """The failure mode this whole module exists to prevent.

        Valhalla reports its own surface and cycle lane for every edge. Reading
        them would turn silence into a claim across most of rural Ireland.
        """
        segments = segments_from_traced_edges(
            [edge(999, 0, 3, engine_surface="paved_smooth", engine_cycle_lane="none")],
            SHAPE,
            {},
        )

        assert segments[0].surface_status is KnowledgeStatus.UNKNOWN
        assert segments[0].surface_family is SurfaceFamily.UNKNOWN
        assert segments[0].cycle_lane_status is KnowledgeStatus.UNKNOWN
        assert segments[0].cycle_lane is None
        assert segments[0].bicycle_access_status is KnowledgeStatus.UNKNOWN

    def test_a_stored_way_supplies_its_attributes(self) -> None:
        segments = segments_from_traced_edges([edge(42, 0, 3)], SHAPE, {42: SURVEYED})

        assert segments[0].surface_family is SurfaceFamily.PAVED
        assert segments[0].surface_status is KnowledgeStatus.KNOWN
        assert segments[0].road_class is RoadClass.TERTIARY

    def test_an_edge_with_no_way_id_is_unknown(self) -> None:
        segments = segments_from_traced_edges([edge(None, 0, 3)], SHAPE, {})

        assert segments[0].surface_status is KnowledgeStatus.UNKNOWN


class TestFerries:
    def test_the_engine_reveals_a_ferry_even_when_the_way_is_missing(self) -> None:
        """Structural, not attributive: it says which graph edge was used.

        A missed ferry is a route that cannot be ridden, so this one signal is
        read where surface and access are not.
        """
        segments = segments_from_traced_edges([edge(1, 0, 3, use="ferry")], SHAPE, {})

        assert segments[0].is_ferry is True

    def test_a_stored_ferry_is_a_ferry(self) -> None:
        stored = WayAttributes(is_ferry=True)
        segments = segments_from_traced_edges([edge(1, 0, 3)], SHAPE, {1: stored})

        assert segments[0].is_ferry is True

    def test_an_unattributed_road_edge_reports_unknown_not_no_ferry(self) -> None:
        """Nothing was observed either way, and that is what gets recorded."""
        segments = segments_from_traced_edges([edge(1, 0, 3)], SHAPE, {})

        assert segments[0].is_ferry is None


class TestReport:
    def test_a_route_over_unimported_ways_reports_the_gap(self) -> None:
        report = AttributionReport(
            edges_traced=10,
            edges_matched=2,
            distance_m=1000.0,
            distance_matched_m=200.0,
            ways_missing=8,
        )

        assert report.matched_share == pytest.approx(0.2)
        assert report.as_dict()["ways_missing_from_network"] == 8

    def test_an_empty_route_has_no_share_rather_than_zero(self) -> None:
        report = AttributionReport(0, 0, 0.0, 0.0, 0)

        assert report.matched_share is None
        assert report.as_dict()["matched_share"] is None


class TestTraceChunking:
    """Valhalla's trace limits are far tighter than its bicycle routing limits."""

    def test_a_short_shape_is_one_chunk(self) -> None:
        chunks = _chunk_shape(SHAPE, 16_000, _TRACE_MAX_DISTANCE_M)

        assert len(chunks) == 1
        assert chunks[0] == (0, SHAPE)

    def test_chunks_share_their_boundary_vertex(self) -> None:
        """Without the shared vertex, the edge spanning it is lost from both."""
        chunks = _chunk_shape(SHAPE, 3, _TRACE_MAX_DISTANCE_M)

        assert len(chunks) > 1
        for (_, first), (start, second) in pairwise(chunks):
            assert first[-1] == second[0]
            assert SHAPE[start] == second[0]

    def test_offsets_index_into_the_whole_shape(self) -> None:
        """Trace reports indices per chunk; they must be translated back."""
        chunks = _chunk_shape(SHAPE, 3, _TRACE_MAX_DISTANCE_M)

        for start, points in chunks:
            assert SHAPE[start : start + len(points)] == points

    def test_the_whole_shape_is_covered(self) -> None:
        chunks = _chunk_shape(SHAPE, 3, _TRACE_MAX_DISTANCE_M)
        covered: set[int] = set()
        for start, points in chunks:
            covered.update(range(start, start + len(points)))

        assert covered == set(range(len(SHAPE)))

    def test_a_long_route_is_split_by_distance(self) -> None:
        """The Wild Atlantic Way is ~2,500 km against a 200 km trace limit."""
        # A degree of latitude is ~111 km, so this spans roughly 550 km.
        long_shape = tuple(LatLon(lat=51.5 + i * 0.05, lon=-9.5) for i in range(100))

        chunks = _chunk_shape(long_shape, 16_000, _TRACE_MAX_DISTANCE_M)

        assert len(chunks) >= 3
        assert chunks[0][0] == 0
        assert chunks[-1][1][-1] == long_shape[-1]

    def test_a_two_point_shape_is_not_dropped(self) -> None:
        pair = SHAPE[:2]

        assert _chunk_shape(pair, 16_000, _TRACE_MAX_DISTANCE_M) == [(0, pair)]
