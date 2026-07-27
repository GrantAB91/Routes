"""OSM tag interpretation tests (§6.3, §6.4, §2.6).

Every test here defends one of two rules: a missing tag is unknown rather than
a default, and an explicit "no" is not the same as silence. Between them they
are what stops Contour manufacturing facts about the tens of thousands of
kilometres of Irish lane that nobody has surveyed.
"""

from __future__ import annotations

from contour_api.ingestion.osm_tags import (
    interpret_bicycle_access,
    interpret_boolean,
    interpret_cycle_infrastructure,
    interpret_road_class,
    interpret_speed_limit,
    interpret_surface,
    is_routable,
    oneway_for_bicycles,
    preserved,
)
from contour_api.models.enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)


class TestSurface:
    def test_an_untagged_lane_is_unknown_not_paved(self) -> None:
        """The single most consequential default in cycling data.

        Most rural Irish lanes carry no surface tag. Reading that as "paved"
        invents a fact about the majority of the network.
        """
        interpreted, family = interpret_surface({"highway": "unclassified"})

        assert interpreted.status is KnowledgeStatus.UNKNOWN
        assert family is SurfaceFamily.UNKNOWN

    def test_a_surveyed_surface_is_known_and_kept_verbatim(self) -> None:
        interpreted, family = interpret_surface({"surface": "asphalt"})

        assert interpreted.status is KnowledgeStatus.KNOWN
        assert interpreted.raw == "asphalt"
        assert family is SurfaceFamily.PAVED

    def test_sett_counts_as_paved_but_keeps_its_own_name(self) -> None:
        """A grouping for distance metrics must not overwrite the survey."""
        interpreted, family = interpret_surface({"surface": "sett"})

        assert family is SurfaceFamily.PAVED
        assert interpreted.raw == "sett"

    def test_gravel_is_unpaved(self) -> None:
        _, family = interpret_surface({"surface": "fine_gravel"})

        assert family is SurfaceFamily.UNPAVED

    def test_an_unrecognised_surface_stays_known_but_unclassified(self) -> None:
        """A surveyor wrote something; Contour just cannot bucket it."""
        interpreted, family = interpret_surface({"surface": "invented_surface"})

        assert interpreted.status is KnowledgeStatus.KNOWN
        assert interpreted.raw == "invented_surface"
        assert family is SurfaceFamily.UNKNOWN

    def test_tracktype_places_the_family_without_claiming_a_surface(self) -> None:
        """tracktype is a firmness scale, not a material.

        grade3 says the track is soft, which is enough to call it unpaved for a
        distance metric, and says nothing about what it is made of.
        """
        interpreted, family = interpret_surface({"highway": "track", "tracktype": "grade3"})

        assert family is SurfaceFamily.UNPAVED
        assert interpreted.status is KnowledgeStatus.UNKNOWN


class TestAccess:
    def test_no_access_tag_is_unknown_not_permitted(self) -> None:
        """Contour will not call a road legal because nobody said it was not."""
        assert interpret_bicycle_access({"highway": "residential"}).status is (
            KnowledgeStatus.UNKNOWN
        )

    def test_explicit_prohibition_is_known(self) -> None:
        interpreted = interpret_bicycle_access({"bicycle": "no"})

        assert interpreted.status is KnowledgeStatus.KNOWN
        assert interpreted.value is BicycleAccess.NO

    def test_the_specific_tag_wins_over_the_general(self) -> None:
        """bicycle=yes on access=private means bicycles may pass."""
        interpreted = interpret_bicycle_access({"access": "private", "bicycle": "yes"})

        assert interpreted.value is BicycleAccess.YES

    def test_a_cycleway_is_designated_by_definition(self) -> None:
        """This is the tag's meaning, not an inference from context."""
        interpreted = interpret_bicycle_access({"highway": "cycleway"})

        assert interpreted.status is KnowledgeStatus.KNOWN
        assert interpreted.value is BicycleAccess.DESIGNATED

    def test_an_unrecognised_access_value_is_recorded_but_not_resolved(self) -> None:
        interpreted = interpret_bicycle_access({"bicycle": "some_new_value"})

        assert interpreted.status is KnowledgeStatus.UNKNOWN
        assert "some_new_value" in (interpreted.raw or "")


class TestCycleInfrastructure:
    def test_absent_is_distinct_from_unrecorded(self) -> None:
        """The distinction Valhalla's own vocabulary loses.

        Its CycleLane::kNone covers both "surveyed, none here" and "nobody
        looked". A rider planning around infrastructure needs to know which.
        """
        surveyed_none = interpret_cycle_infrastructure({"cycleway": "no"})
        nobody_looked = interpret_cycle_infrastructure({"highway": "residential"})

        assert surveyed_none.status is KnowledgeStatus.KNOWN
        assert surveyed_none.value is CycleLaneKind.ABSENT
        assert nobody_looked.status is KnowledgeStatus.UNKNOWN
        assert nobody_looked.value is None

    def test_a_lane_is_dedicated(self) -> None:
        assert interpret_cycle_infrastructure({"cycleway": "lane"}).value is (
            CycleLaneKind.DEDICATED
        )

    def test_a_track_is_separated(self) -> None:
        assert interpret_cycle_infrastructure({"cycleway:right": "track"}).value is (
            CycleLaneKind.SEPARATED
        )

    def test_a_shared_lane_is_shared(self) -> None:
        assert interpret_cycle_infrastructure({"cycleway": "shared_lane"}).value is (
            CycleLaneKind.SHARED
        )


class TestSpeedLimit:
    def test_bare_numbers_are_kilometres_per_hour(self) -> None:
        assert interpret_speed_limit({"maxspeed": "80"}).value == 80

    def test_mph_is_converted(self) -> None:
        """Northern Ireland posts mph and the corridor crosses into it."""
        assert interpret_speed_limit({"maxspeed": "30 mph"}).value == 48

    def test_explicit_kmh_is_parsed(self) -> None:
        assert interpret_speed_limit({"maxspeed": "100 km/h"}).value == 100

    def test_non_numeric_values_are_kept_but_unresolved(self) -> None:
        interpreted = interpret_speed_limit({"maxspeed": "walk"})

        assert interpreted.status is KnowledgeStatus.UNKNOWN
        assert interpreted.raw == "walk"

    def test_absent_is_unknown_not_a_national_default(self) -> None:
        assert interpret_speed_limit({}).status is KnowledgeStatus.UNKNOWN


class TestDirection:
    def test_contraflow_cycling_is_honoured(self) -> None:
        """oneway:bicycle=no on a one-way street is common and legally binding.

        Reading only `oneway` would tell a rider they cannot use a street they
        are explicitly permitted to ride.
        """
        interpreted = oneway_for_bicycles({"oneway": "yes", "oneway:bicycle": "no"})

        assert interpreted.status is KnowledgeStatus.KNOWN
        assert interpreted.value is False

    def test_an_opposite_lane_implies_contraflow(self) -> None:
        interpreted = oneway_for_bicycles({"oneway": "yes", "cycleway": "opposite_lane"})

        assert interpreted.value is False

    def test_a_plain_oneway_applies(self) -> None:
        assert oneway_for_bicycles({"oneway": "yes"}).value is True


class TestBooleans:
    def test_no_is_a_fact_and_absent_is_not(self) -> None:
        assert interpret_boolean({"lit": "no"}, "lit").value is False
        assert interpret_boolean({"lit": "no"}, "lit").status is KnowledgeStatus.KNOWN
        assert interpret_boolean({}, "lit").status is KnowledgeStatus.UNKNOWN

    def test_a_non_boolean_value_is_kept_but_unresolved(self) -> None:
        """bridge=viaduct is meaningful; it is just not a yes/no."""
        interpreted = interpret_boolean({"bridge": "viaduct"}, "bridge")

        assert interpreted.status is KnowledgeStatus.UNKNOWN
        assert interpreted.raw == "viaduct"


class TestRoutability:
    def test_roads_and_paths_are_routable(self) -> None:
        assert is_routable({"highway": "residential"})
        assert is_routable({"highway": "cycleway"})
        assert is_routable({"highway": "track"})

    def test_ferries_are_routable(self) -> None:
        assert is_routable({"route": "ferry"})

    def test_non_highways_are_not(self) -> None:
        assert not is_routable({"building": "yes"})
        assert not is_routable({"highway": "bus_stop"})

    def test_road_class_maps_links_to_their_parent(self) -> None:
        assert interpret_road_class({"highway": "primary_link"}).value is RoadClass.PRIMARY


def test_preserved_tags_keep_the_source_readable() -> None:
    """A segment must be explainable from what the source actually said."""
    tags = {
        "highway": "tertiary",
        "surface": "asphalt",
        "maxspeed": "80",
        "building": "yes",
    }

    kept = preserved(tags)

    assert kept["highway"] == "tertiary"
    assert kept["surface"] == "asphalt"
    # Tags irrelevant to cycling are not carried.
    assert "building" not in kept
