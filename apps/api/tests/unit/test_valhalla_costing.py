"""Tests for the Contour → Valhalla costing mapping.

These pin the decisions documented in ``providers/valhalla.py``: the two
preference inversions, the ``avoid_bad_surfaces`` clamp, and the rule that
``alternates`` is only requested where the engine supports it.
"""

from __future__ import annotations

import pytest

from contour_api.models.enums import BicycleType
from contour_api.providers.routing import (
    CostingPreferences,
    ExclusionSet,
    LatLon,
    RouteRequest,
    RoutingLocation,
)
from contour_api.providers.valhalla import ValhallaProvider

GALWAY = RoutingLocation(point=LatLon(lat=53.2707, lon=-9.0568))
CLIFDEN = RoutingLocation(point=LatLon(lat=53.4890, lon=-10.0200))
WESTPORT = RoutingLocation(point=LatLon(lat=53.8008, lon=-9.5229))


@pytest.fixture
def provider() -> ValhallaProvider:
    return ValhallaProvider("http://127.0.0.1:8002")


def test_defaults_match_the_documented_engine_defaults(provider: ValhallaProvider) -> None:
    """An unset Contour preference must not become a Contour opinion."""
    options = provider.build_costing_options(CostingPreferences())

    assert options["bicycle_type"] == "hybrid"
    assert options["use_roads"] == 0.25
    assert options["use_hills"] == 0.25
    assert options["use_ferry"] == 0.5
    assert options["avoid_bad_surfaces"] == 0.25


def test_avoid_hills_is_inverted_onto_use_hills(provider: ValhallaProvider) -> None:
    """Contour states "avoid hills"; Valhalla states willingness to climb them."""
    strongly_avoid = provider.build_costing_options(CostingPreferences(avoid_hills=1.0))
    happy_to_climb = provider.build_costing_options(CostingPreferences(avoid_hills=0.0))

    assert strongly_avoid["use_hills"] == 0.0
    assert happy_to_climb["use_hills"] == 1.0


def test_cycle_infrastructure_preference_is_inverted_onto_use_roads(
    provider: ValhallaProvider,
) -> None:
    """Valhalla has no cycle-infrastructure option; a low use_roads is the proxy."""
    options = provider.build_costing_options(
        CostingPreferences(prefer_cycle_infrastructure=0.9)
    )

    assert options["use_roads"] == pytest.approx(0.1)


def test_explicit_use_roads_wins_over_the_infrastructure_proxy(
    provider: ValhallaProvider,
) -> None:
    options = provider.build_costing_options(
        CostingPreferences(use_roads=0.8, prefer_cycle_infrastructure=0.9)
    )

    assert options["use_roads"] == 0.8


def test_avoid_bad_surfaces_never_reaches_one(provider: ValhallaProvider) -> None:
    """At exactly 1.0 the engine disallows bad surfaces including the endpoints.

    That turns a strong preference into an unroutable request whenever a start
    or finish sits on an unpaved lane — common on the Wild Atlantic Way. Surface
    limits are enforced by validation instead, so the value is clamped below 1.
    """
    options = provider.build_costing_options(
        CostingPreferences(avoid_bad_surfaces=1.0)
    )

    assert options["avoid_bad_surfaces"] < 1.0
    assert options["avoid_bad_surfaces"] == pytest.approx(0.95)


def test_preferring_gravel_stops_the_engine_steering_away_from_it(
    provider: ValhallaProvider,
) -> None:
    options = provider.build_costing_options(
        CostingPreferences(bicycle_type=BicycleType.CROSS, prefer_gravel=1.0)
    )

    assert options["bicycle_type"] == "cross"
    assert options["avoid_bad_surfaces"] == 0.0


def test_ebike_maps_to_hybrid_because_valhalla_has_no_ebike_type(
    provider: ValhallaProvider,
) -> None:
    """Documented in §7.2.6: no battery model is implied by the e-bike profile."""
    options = provider.build_costing_options(
        CostingPreferences(bicycle_type=BicycleType.EBIKE)
    )

    assert options["bicycle_type"] == "hybrid"


def test_shortest_discards_other_preferences(provider: ValhallaProvider) -> None:
    """`shortest` is documented to disable all other costings and penalties.

    Sending it alongside preferences would misrepresent what was asked for, so
    the mapping drops them explicitly rather than letting the engine ignore them.
    """
    options = provider.build_costing_options(
        CostingPreferences(shortest=True, avoid_hills=1.0, use_roads=0.1)
    )

    assert options == {"bicycle_type": "hybrid", "shortest": True}


class TestRequestPayload:
    """Payload construction, exercised through the documented request shape."""

    @staticmethod
    def _payload(provider: ValhallaProvider, request: RouteRequest) -> dict:
        captured: dict = {}

        async def fake_post(path: str, payload: dict) -> dict:
            captured.update(payload)
            return {"trip": {"legs": [], "summary": {"length": 0.0}}}

        provider._post = fake_post  # type: ignore[method-assign]
        import asyncio

        asyncio.run(provider.route(request))
        return captured

    def test_alternates_requested_for_a_two_point_route(
        self, provider: ValhallaProvider
    ) -> None:
        payload = self._payload(
            provider, RouteRequest(locations=(GALWAY, CLIFDEN), alternates=2)
        )

        assert payload["alternates"] == 2

    def test_alternates_omitted_for_a_multipoint_route(
        self, provider: ValhallaProvider
    ) -> None:
        """Upstream documents alternates as unsupported with via points.

        Sending the parameter anyway would have the solver believe it asked for
        variety it can never receive, so it is omitted and the solver's own
        corridor-penalised re-solves supply the alternatives instead.
        """
        payload = self._payload(
            provider,
            RouteRequest(locations=(GALWAY, CLIFDEN, WESTPORT), alternates=3),
        )

        assert "alternates" not in payload

    def test_exclusion_polygons_use_lon_lat_order(self, provider: ValhallaProvider) -> None:
        """exclude_polygons is [lon, lat]; locations are {lat, lon}. Easy to swap."""
        ring = (LatLon(lat=53.0, lon=-9.5), LatLon(lat=53.1, lon=-9.5), LatLon(lat=53.1, lon=-9.4))
        payload = self._payload(
            provider,
            RouteRequest(
                locations=(GALWAY, CLIFDEN),
                exclusions=ExclusionSet(polygons=(ring,)),
            ),
        )

        assert payload["exclude_polygons"] == [[[-9.5, 53.0], [-9.5, 53.1], [-9.4, 53.1]]]
        assert payload["locations"][0] == {"lat": 53.2707, "lon": -9.0568, "type": "break"}

    def test_excluded_segments_are_sent_as_locations(self, provider: ValhallaProvider) -> None:
        """Upstream: exclude_locations is "much more efficient" for specific roads."""
        payload = self._payload(
            provider,
            RouteRequest(
                locations=(GALWAY, CLIFDEN),
                exclusions=ExclusionSet(locations=(LatLon(lat=53.3, lon=-9.6),)),
            ),
        )

        assert payload["exclude_locations"] == [{"lat": 53.3, "lon": -9.6}]


def test_a_route_needs_at_least_two_locations() -> None:
    with pytest.raises(ValueError, match="origin and a destination"):
        RouteRequest(locations=(GALWAY,))
