"""Way-level import translation (§6.4, §2.6).

These cover the step between a tag dictionary and a database row: geometry,
length, which ways are kept, and — the part that matters most — that nothing
acquires a value the source did not supply.
"""

from __future__ import annotations

import pytest

from contour_api.ingestion.osm_import import (
    ImportCounters,
    NetworkRef,
    WayRecord,
    _parse_width,
    interpret_way,
)
from contour_api.models.enums import (
    BicycleAccess,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)

# A short stretch of coast road near Louisburgh, roughly 1.1 km.
COAST = ((-9.8100, 53.7600), (-9.8000, 53.7620), (-9.7900, 53.7640))


def a_way(**tags: str) -> WayRecord:
    record = interpret_way(1, {"highway": "unclassified", **tags}, COAST)
    assert record is not None
    return record


class TestSelection:
    def test_a_building_is_not_part_of_the_network(self) -> None:
        assert interpret_way(1, {"building": "yes"}, COAST) is None

    def test_a_ferry_is(self) -> None:
        """Dropping ferries would produce routes that cannot be ridden."""
        record = interpret_way(1, {"route": "ferry"}, COAST)

        assert record is not None
        assert record.is_ferry is True

    def test_a_way_with_one_node_has_no_geometry_and_is_skipped(self) -> None:
        assert interpret_way(1, {"highway": "residential"}, COAST[:1]) is None


class TestGeometry:
    def test_length_is_measured_along_the_way(self) -> None:
        # Two ~0.55 km hops along the coast.
        assert 900 < a_way().length_m < 1400

    def test_ewkt_carries_the_srid(self) -> None:
        """Without the SRID the geometry lands in PostGIS as srid=0."""
        ewkt = a_way().ewkt()

        assert ewkt.startswith("SRID=4326;LINESTRING(")
        assert ewkt.count(",") == len(COAST) - 1

    def test_coordinates_are_written_longitude_first(self) -> None:
        """The axis order bug that silently puts Ireland in Somalia."""
        ewkt = a_way().ewkt()
        first = ewkt.split("(")[1].split(",")[0].split()

        assert float(first[0]) == pytest.approx(-9.81, abs=1e-4)
        assert float(first[1]) == pytest.approx(53.76, abs=1e-4)


class TestNothingIsInvented:
    def test_an_untagged_lane_arrives_unknown_across_the_board(self) -> None:
        """The most common way in the extract, and the one most easily faked."""
        record = a_way()

        assert record.surface_status is KnowledgeStatus.UNKNOWN
        assert record.surface_family is SurfaceFamily.UNKNOWN
        assert record.surface_tag_raw is None
        assert record.bicycle_access_status is KnowledgeStatus.UNKNOWN
        assert record.speed_limit_status is KnowledgeStatus.UNKNOWN
        assert record.speed_limit_kph is None
        assert record.cycle_lane_status is KnowledgeStatus.UNKNOWN
        assert record.cycle_lane is None
        assert record.lit is None
        assert record.shoulder is None

    def test_a_surveyed_way_carries_both_the_reading_and_the_tag(self) -> None:
        record = a_way(surface="asphalt", maxspeed="80", bicycle="yes")

        assert record.surface_family is SurfaceFamily.PAVED
        assert record.surface_tag_raw == "asphalt"
        assert record.speed_limit_kph == 80
        assert record.bicycle_access is BicycleAccess.YES
        # And the source is still readable verbatim.
        assert record.tags["surface"] == "asphalt"

    def test_road_class_is_read_not_guessed_from_width(self) -> None:
        record = a_way(highway="tertiary", width="6")

        assert record.road_class is RoadClass.TERTIARY
        assert record.road_class_status is KnowledgeStatus.KNOWN


class TestCompleteness:
    def test_completeness_measures_evidence_not_quality(self) -> None:
        # `highway` is itself one of the five, so a way that carries only a
        # highway tag already scores 1/5 — it has been classified, just not
        # surveyed for anything else.
        bare = a_way()
        surveyed = a_way(
            surface="gravel",
            bicycle="yes",
            highway="track",
            maxspeed="60",
            cycleway="no",
        )

        assert bare.attribute_completeness == pytest.approx(0.2)
        assert surveyed.attribute_completeness == 1.0
        # A fully surveyed track is still a track.
        assert surveyed.surface_family is SurfaceFamily.UNPAVED

    def test_a_way_with_no_highway_tag_at_all_scores_zero(self) -> None:
        record = interpret_way(1, {"route": "ferry"}, COAST)

        assert record is not None
        assert record.attribute_completeness == 0.0


class TestShoulder:
    def test_a_side_value_states_one_exists(self) -> None:
        assert a_way(shoulder="right").shoulder is True

    def test_no_is_a_fact(self) -> None:
        assert a_way(shoulder="no").shoulder is False

    def test_absent_is_not(self) -> None:
        assert a_way().shoulder is None


class TestWidth:
    @pytest.mark.parametrize(("value", "expected"), [("3", 3.0), ("2.5 m", 2.5), ("4m", 4.0)])
    def test_metre_values_are_read(self, value: str, expected: float) -> None:
        assert _parse_width(value) == expected

    @pytest.mark.parametrize("value", [None, "", "wide", "3'6\"", "0", "120"])
    def test_anything_else_is_left_unset_rather_than_guessed(self, value: str | None) -> None:
        assert _parse_width(value) is None


class TestNetworkMembership:
    def test_relation_membership_is_recorded_with_its_relation_id(self) -> None:
        """So a claim of EuroVelo membership can be traced to the relation."""
        ref = NetworkRef(relation_id=1234, network="ncn", ref="EV1", name="EuroVelo 1")
        record = interpret_way(1, {"highway": "unclassified"}, COAST, network_refs=(ref,))

        assert record is not None
        assert record.network_refs[0].as_dict()["relation_id"] == 1234


class TestCounters:
    def test_coverage_is_reported_by_distance_as_well_as_count(self) -> None:
        """Counting ways overstates coverage when the surveyed ones are short."""
        counters = ImportCounters()
        counters.observe(a_way(surface="asphalt"))
        counters.observe(a_way())

        coverage = next(entry for entry in counters.as_log() if entry["stage"] == "coverage")

        assert coverage["surface_surveyed_ways"] == 1
        assert coverage["surface_surveyed_share_by_distance"] == pytest.approx(0.5, abs=0.01)

    def test_an_empty_import_reports_no_share_rather_than_zero(self) -> None:
        """0% surveyed and nothing seen are different results."""
        coverage = next(
            entry for entry in ImportCounters().as_log() if entry["stage"] == "coverage"
        )

        assert coverage["surface_surveyed_share_by_distance"] is None
