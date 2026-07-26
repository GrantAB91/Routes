"""Constraint evaluation and validation tests.

The property under test throughout is that missing data never produces a pass.
Every constraint here has a third outcome between satisfied and violated, and
these tests exist to stop that third outcome quietly collapsing into the first
— which is how a routing tool ends up telling someone an unsurveyed boreen is
paved and legal.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from contour_api.models.enums import (
    BicycleAccess,
    FeasibilityVerdict,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)
from contour_api.providers.routing import LatLon
from contour_api.routing.constraints import (
    ConstraintSet,
    FerryUse,
    LegalBicycleAccess,
    MaxDetour,
    MaxGradient,
    MaxSurfaceDistance,
    MaxTotalDistance,
    MaxUnknownSurfaceDistance,
    Outcome,
    ProhibitedRoadClasses,
)
from contour_api.routing.model import RouteView, SegmentView, shared_distance_m
from contour_api.routing.validation import RouteValidator

ORIGIN = LatLon(lat=53.2707, lon=-9.0568)
DESTINATION = LatLon(lat=53.4890, lon=-10.0200)


def segment(
    index: int,
    *,
    start_m: float = 0.0,
    distance_m: float = 1000.0,
    grade: float | None = 5.0,
    elevation_known: bool = True,
    surface: SurfaceFamily = SurfaceFamily.PAVED,
    surface_known: bool = True,
    access: BicycleAccess = BicycleAccess.YES,
    access_known: bool = True,
    road_class: RoadClass = RoadClass.TERTIARY,
    road_class_known: bool = True,
    is_ferry: bool = False,
    against_oneway: bool = False,
    start: LatLon | None = None,
    end: LatLon | None = None,
    edge_id: str | None = None,
) -> SegmentView:
    return SegmentView(
        index=index,
        start_distance_m=start_m,
        distance_m=distance_m,
        max_grade_percent=grade,
        elevation_status=(KnowledgeStatus.KNOWN if elevation_known else KnowledgeStatus.UNKNOWN),
        surface_family=surface,
        surface_status=KnowledgeStatus.KNOWN if surface_known else KnowledgeStatus.UNKNOWN,
        bicycle_access=access,
        bicycle_access_status=(KnowledgeStatus.KNOWN if access_known else KnowledgeStatus.UNKNOWN),
        road_class=road_class,
        road_class_status=(KnowledgeStatus.KNOWN if road_class_known else KnowledgeStatus.UNKNOWN),
        is_ferry=is_ferry,
        oneway_bicycle=False,
        traversed_against_oneway=against_oneway,
        coordinates=(start or ORIGIN, end or DESTINATION),
        graph_edge_id=edge_id,
    )


def route(*segments: SegmentView, **kwargs) -> RouteView:
    """Build a route, laying segments end to end.

    Start distances are recomputed here so tests can declare only the lengths
    they care about without hand-maintaining cumulative offsets.
    """
    laid_out: list[SegmentView] = []
    cursor = 0.0
    for index, seg in enumerate(segments):
        laid_out.append(replace(seg, index=index, start_distance_m=cursor))
        cursor += seg.distance_m

    return RouteView(
        segments=tuple(laid_out),
        origin=kwargs.pop("origin", ORIGIN),
        destination=kwargs.pop("destination", DESTINATION),
        **kwargs,
    )


class TestMaxGradient:
    def test_violation_names_the_offending_segments(self) -> None:
        result = MaxGradient(limit_percent=12.0).evaluate(
            route(
                segment(0, grade=8.0),
                segment(1, grade=15.4),
                segment(2, grade=6.0),
            )
        )

        assert result.outcome is Outcome.VIOLATED
        assert result.segment_indices == (1,)
        assert result.observed == pytest.approx(15.4)
        assert "15.4" in result.detail

    def test_missing_elevation_is_unevaluable_not_satisfied(self) -> None:
        """A wall is most likely to hide exactly where nothing was measured."""
        result = MaxGradient(limit_percent=12.0).evaluate(
            route(
                segment(0, grade=5.0),
                segment(1, grade=None, elevation_known=False, distance_m=4000.0),
            )
        )

        assert result.outcome is Outcome.UNEVALUABLE
        assert result.affected_distance_m == pytest.approx(4000.0)
        assert "no elevation data" in result.detail

    def test_measured_compliance_is_satisfied(self) -> None:
        result = MaxGradient(limit_percent=12.0).evaluate(
            route(segment(0, grade=5.0), segment(1, grade=9.0))
        )

        assert result.outcome is Outcome.SATISFIED

    def test_a_real_violation_outranks_missing_data(self) -> None:
        """A known breach is reported even when other segments are unmeasured."""
        result = MaxGradient(limit_percent=10.0).evaluate(
            route(
                segment(0, grade=None, elevation_known=False),
                segment(1, grade=18.0),
            )
        )

        assert result.outcome is Outcome.VIOLATED


class TestSurface:
    def test_unknown_surface_does_not_count_toward_the_unpaved_limit(self) -> None:
        """Counting unknown as unpaved would invent a surface, either way."""
        result = MaxSurfaceDistance(family=SurfaceFamily.UNPAVED, limit_m=5000.0).evaluate(
            route(
                segment(0, surface=SurfaceFamily.UNPAVED, distance_m=2000.0),
                segment(1, surface_known=False, distance_m=2000.0),
            )
        )

        assert result.observed == pytest.approx(2000.0)

    def test_unknown_surface_blocks_a_claim_of_compliance(self) -> None:
        """If the unknown stretch could breach the limit, compliance is unproven."""
        result = MaxSurfaceDistance(family=SurfaceFamily.UNPAVED, limit_m=5000.0).evaluate(
            route(
                segment(0, surface=SurfaceFamily.UNPAVED, distance_m=4000.0),
                segment(1, surface_known=False, distance_m=8000.0),
            )
        )

        assert result.outcome is Outcome.UNEVALUABLE
        assert "no surface data" in result.detail

    def test_small_unknown_stretch_cannot_breach_so_it_is_satisfied(self) -> None:
        """Where the unknown could not possibly breach, compliance is provable."""
        result = MaxSurfaceDistance(family=SurfaceFamily.UNPAVED, limit_m=50_000.0).evaluate(
            route(
                segment(0, surface=SurfaceFamily.UNPAVED, distance_m=1000.0),
                segment(1, surface_known=False, distance_m=500.0),
            )
        )

        assert result.outcome is Outcome.SATISFIED

    def test_unknown_tolerance_is_always_evaluable(self) -> None:
        """How much is unknown is itself known."""
        result = MaxUnknownSurfaceDistance(limit_m=1000.0).evaluate(
            route(
                segment(0, surface_known=False, distance_m=3000.0),
                segment(1, surface=SurfaceFamily.PAVED, distance_m=1000.0),
            )
        )

        assert result.outcome is Outcome.VIOLATED
        assert result.observed == pytest.approx(3000.0)

    def test_composition_reports_unknown_as_its_own_share(self) -> None:
        composition = route(
            segment(0, surface=SurfaceFamily.PAVED, distance_m=1000.0),
            segment(1, surface_known=False, distance_m=3000.0),
        ).surface_composition_m()

        assert composition["paved"] == pytest.approx(1000.0)
        assert composition["unknown"] == pytest.approx(3000.0)


class TestAccess:
    def test_unknown_access_is_not_permission(self) -> None:
        """Contour will not call a road legal because nobody said it was not."""
        result = LegalBicycleAccess().evaluate(
            route(segment(0), segment(1, access_known=False, distance_m=2000.0))
        )

        assert result.outcome is Outcome.UNEVALUABLE
        assert "no recorded bicycle access" in result.detail

    def test_explicit_prohibition_is_a_violation(self) -> None:
        result = LegalBicycleAccess().evaluate(
            route(segment(0), segment(1, access=BicycleAccess.NO))
        )

        assert result.outcome is Outcome.VIOLATED
        assert result.segment_indices == (1,)

    def test_designated_access_satisfies(self) -> None:
        result = LegalBicycleAccess().evaluate(route(segment(0, access=BicycleAccess.DESIGNATED)))

        assert result.outcome is Outcome.SATISFIED

    def test_motorway_use_is_a_violation(self) -> None:
        result = ProhibitedRoadClasses().evaluate(
            route(segment(0), segment(1, road_class=RoadClass.MOTORWAY))
        )

        assert result.outcome is Outcome.VIOLATED
        assert "motorway" in result.detail


class TestFerryAndDistance:
    def test_forbidden_ferry_is_a_violation(self) -> None:
        result = FerryUse(allowed=False).evaluate(route(segment(0), segment(1, is_ferry=True)))

        assert result.outcome is Outcome.VIOLATED

    def test_required_ferry_missing_is_a_violation(self) -> None:
        result = FerryUse(required=True).evaluate(route(segment(0)))

        assert result.outcome is Outcome.VIOLATED

    def test_total_distance_limit(self) -> None:
        result = MaxTotalDistance(limit_m=1500.0).evaluate(
            route(segment(0, distance_m=1000.0), segment(1, distance_m=1000.0))
        )

        assert result.outcome is Outcome.VIOLATED
        assert result.observed == pytest.approx(2000.0)

    def test_detour_without_a_reference_is_unevaluable(self) -> None:
        result = MaxDetour(reference_distance_m=0.0, max_ratio=1.15).evaluate(route(segment(0)))

        assert result.outcome is Outcome.UNEVALUABLE


class TestVerdicts:
    def test_all_known_and_compliant_is_fully_satisfied(self) -> None:
        validator = RouteValidator(
            ConstraintSet([MaxGradient(limit_percent=12.0), LegalBicycleAccess()])
        )
        subject = RouteView(
            segments=(segment(0, start=ORIGIN, end=DESTINATION, grade=6.0),),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.FULLY_SATISFIED
        assert not outcome.violations

    def test_a_deployment_limitation_is_reported_without_downgrading_the_verdict(
        self,
    ) -> None:
        """A warning that fires on every route is a warning nobody reads.

        Contour has no hydrography source, so the water-crossing check cannot
        run for any route at all. That is a standing limitation of the
        deployment, not a fact missing about this route, and it is reported
        separately rather than making every result in the system read
        "satisfied with unknown data".
        """
        validator = RouteValidator(ConstraintSet([MaxGradient(limit_percent=12.0)]))
        subject = RouteView(
            segments=(segment(0, start=ORIGIN, end=DESTINATION, grade=6.0),),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.FULLY_SATISFIED
        # Still disclosed, just not charged against the route.
        limitations = {c.name for c in outcome.unavailable_checks}
        assert "no_unexplained_water_crossing" in limitations
        assert outcome.as_dict()["unavailable_checks"][0]["detail"]

    def test_route_specific_missing_data_still_downgrades(self) -> None:
        """The distinction must not become a way to hide real gaps."""
        validator = RouteValidator(ConstraintSet([MaxGradient(limit_percent=12.0)]))
        subject = RouteView(
            segments=(
                segment(0, start=ORIGIN, end=DESTINATION, grade=None, elevation_known=False),
            ),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.SATISFIED_WITH_UNKNOWN_DATA

    def test_violated_hard_constraint_is_partially_satisfied(self) -> None:
        validator = RouteValidator(ConstraintSet([MaxGradient(limit_percent=8.0)]))
        subject = RouteView(
            segments=(segment(0, start=ORIGIN, end=DESTINATION, grade=14.0),),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.PARTIALLY_SATISFIED
        assert outcome.violations[0].key == "max_gradient_percent"

    def test_exhausted_attempts_turns_violation_into_not_feasible(self) -> None:
        """Only after trying does Contour say a request cannot be met."""
        validator = RouteValidator(ConstraintSet([MaxGradient(limit_percent=8.0)]))
        subject = RouteView(
            segments=(segment(0, start=ORIGIN, end=DESTINATION, grade=14.0),),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject, resolve_attempts=4, exhausted_attempts=True)

        assert outcome.verdict is FeasibilityVerdict.NOT_FEASIBLE

    def test_disconnected_geometry_is_not_feasible(self) -> None:
        far = LatLon(lat=54.5, lon=-8.0)
        validator = RouteValidator(ConstraintSet())
        subject = RouteView(
            segments=(
                segment(0, start=ORIGIN, end=LatLon(lat=53.30, lon=-9.10)),
                segment(1, start=far, end=DESTINATION),
            ),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.NOT_FEASIBLE
        assert any(c.name == "connected_geometry" and not c.passed for c in outcome.checks)

    def test_a_ferry_explains_a_gap(self) -> None:
        """A jump across water is legitimate when a ferry links it."""
        across = LatLon(lat=53.62, lon=-9.90)
        validator = RouteValidator(ConstraintSet())
        subject = RouteView(
            segments=(
                segment(0, start=ORIGIN, end=LatLon(lat=53.30, lon=-9.10)),
                segment(1, start=LatLon(lat=53.30, lon=-9.10), end=across, is_ferry=True),
                segment(2, start=across, end=DESTINATION),
            ),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert all(c.outcome is not Outcome.VIOLATED for c in outcome.checks), [
            c.name for c in outcome.checks if c.outcome is Outcome.VIOLATED
        ]

    def test_wrong_endpoint_is_rejected(self) -> None:
        elsewhere = LatLon(lat=52.0, lon=-8.0)
        validator = RouteValidator(ConstraintSet())
        subject = RouteView(
            segments=(segment(0, start=elsewhere, end=DESTINATION),),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.NOT_FEASIBLE
        assert any("requested origin" in c.detail for c in outcome.checks)

    def test_one_way_violation_is_rejected(self) -> None:
        validator = RouteValidator(ConstraintSet())
        subject = RouteView(
            segments=(segment(0, start=ORIGIN, end=DESTINATION, against_oneway=True),),
            origin=ORIGIN,
            destination=DESTINATION,
        )

        outcome = validator.validate(subject)

        assert outcome.verdict is FeasibilityVerdict.NOT_FEASIBLE


class TestSolverFeedback:
    def test_violating_segments_are_collected_for_exclusion(self) -> None:
        """These indices become the next solve pass's exclusion set."""
        results = ConstraintSet(
            [MaxGradient(limit_percent=10.0), ProhibitedRoadClasses()]
        ).evaluate(
            route(
                segment(0, grade=5.0),
                segment(1, grade=16.0),
                segment(2, road_class=RoadClass.MOTORWAY),
            )
        )

        assert ConstraintSet.violating_segments(results) == {1, 2}

    def test_unevaluable_constraints_produce_no_exclusions(self) -> None:
        """There is nothing to route around when the data is merely absent."""
        results = ConstraintSet([MaxGradient(limit_percent=10.0)]).evaluate(
            route(segment(0, grade=None, elevation_known=False))
        )

        assert ConstraintSet.violating_segments(results) == set()


def test_shared_distance_compares_by_edge_identity() -> None:
    """Parallel roads must not count as shared just because they are close."""
    left = [segment(0, edge_id="a", distance_m=1000.0), segment(1, edge_id="b")]
    right = [segment(0, edge_id="a", distance_m=1000.0), segment(1, edge_id="c")]

    assert shared_distance_m(left, right) == pytest.approx(1000.0)


def test_report_serialises_for_the_api() -> None:
    validator = RouteValidator(ConstraintSet([MaxGradient(limit_percent=8.0)]))
    subject = RouteView(
        segments=(segment(0, start=ORIGIN, end=DESTINATION, grade=14.0),),
        origin=ORIGIN,
        destination=DESTINATION,
    )

    payload = validator.validate(subject).as_dict()

    assert payload["verdict"] == "partially_satisfied"
    assert payload["violations"][0]["constraint_key"] == "max_gradient_percent"
    assert payload["summary"]
