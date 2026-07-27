"""The route generation endpoints (§7.6, §16.7, §21.8).

Two things are checked here without a routing engine: that the request models
translate into the constraints they claim to, and that a switched-off capability
is reported as a switched-off capability rather than as a failure or, worse, as
a route with no elevation and no explanation.
"""

from __future__ import annotations

import pytest

from contour_api.models.enums import RoadClass, SurfaceFamily
from contour_api.routers.routes import Constraints, GenerateRequest, Point, _comparison
from contour_api.routing.constraints import (
    FerryUse,
    LegalBicycleAccess,
    MaxGradient,
    MaxSurfaceDistance,
    MaxTotalDistance,
    MaxUnknownSurfaceDistance,
    ProhibitedRoadClasses,
)


def kinds(constraints) -> set[type]:
    return {type(c) for c in constraints.to_set().constraints}


class TestConstraintTranslation:
    def test_defaults_protect_legality_without_inventing_limits(self) -> None:
        """Unset is unconstrained, not zero.

        Access and motorways are the two that hold by default, because both are
        matters of law rather than preference.
        """
        built = kinds(Constraints())

        assert LegalBicycleAccess in built
        assert ProhibitedRoadClasses in built
        assert MaxGradient not in built
        assert MaxSurfaceDistance not in built
        assert MaxTotalDistance not in built

    def test_each_stated_limit_becomes_its_constraint(self) -> None:
        built = kinds(
            Constraints(
                max_gradient_percent=8,
                max_unpaved_m=20_000,
                max_unknown_surface_m=5_000,
                max_total_distance_m=120_000,
            )
        )

        assert MaxGradient in built
        assert MaxSurfaceDistance in built
        assert MaxUnknownSurfaceDistance in built
        assert MaxTotalDistance in built

    def test_a_constraint_carries_the_words_it_came_from(self) -> None:
        """§7.9: a violation has to be explainable in the user's own terms."""
        constraint = next(
            c
            for c in Constraints(max_gradient_percent=8).to_set().constraints
            if isinstance(c, MaxGradient)
        )

        assert constraint.stated_as == "no gradient above 8%"

    def test_the_unpaved_limit_targets_unpaved_specifically(self) -> None:
        """Not "not paved" — unknown is its own family and must not be swept in."""
        constraint = next(
            c
            for c in Constraints(max_unpaved_m=1000).to_set().constraints
            if isinstance(c, MaxSurfaceDistance)
        )

        assert constraint.family is SurfaceFamily.UNPAVED

    def test_ferries_produce_no_constraint_when_simply_allowed(self) -> None:
        """Allowing something is not requiring it; an unconditional pass is noise."""
        assert FerryUse not in kinds(Constraints())
        assert FerryUse in kinds(Constraints(ferries_allowed=False))
        assert FerryUse in kinds(Constraints(ferries_required=True))

    def test_motorways_are_prohibited_by_default_and_can_be_extended(self) -> None:
        constraint = next(
            c
            for c in Constraints(prohibited_road_classes=[RoadClass.MOTORWAY, RoadClass.TRUNK])
            .to_set()
            .constraints
            if isinstance(c, ProhibitedRoadClasses)
        )

        assert RoadClass.TRUNK in constraint.classes

    def test_access_checking_can_be_turned_off_only_explicitly(self) -> None:
        assert LegalBicycleAccess not in kinds(Constraints(require_legal_bicycle_access=False))


class TestRequestValidation:
    def test_a_gradient_limit_must_be_plausible(self) -> None:
        """Above 40% is not a road, and accepting it would silently pass everything."""
        with pytest.raises(ValueError):
            Constraints(max_gradient_percent=95)

    def test_at_least_two_points_are_required(self) -> None:
        with pytest.raises(ValueError):
            GenerateRequest(locations=[Point(lat=53.0, lon=-9.0)])

    def test_an_a_to_a_request_is_rejected_with_an_explanation(self) -> None:
        """A loop needs to say where it should go; otherwise it is zero metres."""
        with pytest.raises(ValueError, match="via point"):
            GenerateRequest(locations=[Point(lat=53.0, lon=-9.0), Point(lat=53.0, lon=-9.0)])

    def test_a_loop_through_a_via_point_is_accepted(self) -> None:
        request = GenerateRequest(
            locations=[
                Point(lat=53.0, lon=-9.0),
                Point(lat=53.2, lon=-9.4, kind="through"),
                Point(lat=53.0, lon=-9.0),
            ]
        )

        assert len(request.to_route_request().locations) == 3

    def test_avoid_points_become_engine_exclusions(self) -> None:
        request = GenerateRequest(
            locations=[Point(lat=53.0, lon=-9.0), Point(lat=53.5, lon=-9.5)],
            avoid_points=[Point(lat=53.2, lon=-9.2)],
        )

        assert len(request.to_route_request().exclusions.locations) == 1

    def test_coordinates_outside_the_globe_are_rejected(self) -> None:
        with pytest.raises(ValueError):
            Point(lat=91.0, lon=0.0)


class TestComparison:
    def test_a_route_without_elevation_is_not_ranked_as_flattest(self) -> None:
        """The comparison failure that matters: absent read as best.

        A route whose ascent could not be measured must not win a "least
        climbing" comparison by virtue of having no number.
        """
        routed = [
            {
                "index": 0,
                "routed": True,
                "distance_m": 100.0,
                "elevation": {"ascent_m": 500},
                "composition": {"surface_m": {"unpaved": 0.0, "unknown": 0.0}},
                "validation": {"verdict": "fully_satisfied"},
            },
            {
                "index": 1,
                "routed": True,
                "distance_m": 110.0,
                "elevation": None,
                "composition": {"surface_m": {"unpaved": 0.0, "unknown": 110.0}},
                "validation": {"verdict": "satisfied_with_unknown_data"},
            },
        ]

        comparison = _comparison(routed)

        assert comparison["lowest_ascent_index"] == 0
        assert comparison["ascent_m"][1] is None
        assert comparison["routes_without_elevation"] == [1]

    def test_nothing_to_compare_says_so(self) -> None:
        assert _comparison([])["comparable"] is False

    def test_no_measured_ascent_yields_no_winner_rather_than_the_first(self) -> None:
        routed = [
            {
                "index": 0,
                "routed": True,
                "distance_m": 100.0,
                "elevation": None,
                "composition": {"surface_m": {}},
                "validation": {"verdict": "satisfied_with_unknown_data"},
            }
        ]

        assert _comparison(routed)["lowest_ascent_index"] is None
