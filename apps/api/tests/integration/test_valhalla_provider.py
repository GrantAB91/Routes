"""Integration tests against a live ``valhalla_service``.

Run with a service started from ``infra/valhalla/make-config.sh``:

    valhalla_service /opt/contour/valhalla.json 1

Tests that need routable geometry are skipped when no tiles are built, because
tile building requires an OSM extract. They skip with an explicit reason rather
than passing vacuously — a green suite must not imply routing was exercised
when it was not.
"""

from __future__ import annotations

import os

import httpx
import pytest

from contour_api.providers.routing import (
    CostingPreferences,
    LatLon,
    ProviderHealth,
    RouteRequest,
    RoutingLocation,
)
from contour_api.providers.valhalla import ValhallaProvider

pytestmark = pytest.mark.integration

VALHALLA_URL = os.environ.get("CONTOUR_VALHALLA_URL", "http://127.0.0.1:8002")

# Two points a few kilometres apart on the Wild Atlantic Way corridor, used only
# once tiles covering Ireland exist.
CLIFDEN = RoutingLocation(point=LatLon(lat=53.4890, lon=-10.0200))
LETTERFRACK = RoutingLocation(point=LatLon(lat=53.5497, lon=-9.9469))


def _service_available() -> bool:
    try:
        response = httpx.get(f"{VALHALLA_URL}/status", timeout=5.0)
        return response.status_code == 200
    except httpx.HTTPError:
        return False


def _has_tiles() -> bool:
    """Ask the engine whether it can actually route, rather than guessing.

    /status reports the available actions regardless of tile coverage, so
    coverage is probed with a real request instead.
    """
    if not _service_available():
        return False
    try:
        response = httpx.post(
            f"{VALHALLA_URL}/route",
            json={
                "locations": [
                    {"lat": CLIFDEN.point.lat, "lon": CLIFDEN.point.lon},
                    {"lat": LETTERFRACK.point.lat, "lon": LETTERFRACK.point.lon},
                ],
                "costing": "bicycle",
            },
            timeout=20.0,
        )
    except httpx.HTTPError:
        return False
    return response.status_code == 200


requires_service = pytest.mark.skipif(
    not _service_available(),
    reason=f"no valhalla_service at {VALHALLA_URL}; run infra/valhalla/build.sh",
)
requires_tiles = pytest.mark.skipif(
    not _has_tiles(),
    reason=(
        "valhalla_service has no routable tiles for the test area; build them "
        "with infra/valhalla/build-tiles.sh, which needs an OSM extract"
    ),
)


@requires_service
async def test_health_reports_engine_version() -> None:
    provider = ValhallaProvider(VALHALLA_URL)
    try:
        report = await provider.health()
    finally:
        await provider.aclose()

    assert report.status is ProviderHealth.HEALTHY
    # Contour records the engine version against every generated route so a
    # stored result stays explainable after an upgrade; an empty version would
    # break that silently.
    assert report.version
    assert report.version.startswith("3.")


async def test_health_reports_unavailable_rather_than_raising() -> None:
    """A dead engine must degrade to an explicit state, never a fabricated one.

    Port 1 is reserved and never listening, so this exercises the real failure
    path without depending on the service being down.
    """
    provider = ValhallaProvider("http://127.0.0.1:1")
    try:
        report = await provider.health()
    finally:
        await provider.aclose()

    assert report.status is ProviderHealth.UNAVAILABLE
    assert "did not respond" in report.detail


@requires_service
@requires_tiles
async def test_route_returns_decodable_geometry() -> None:
    provider = ValhallaProvider(VALHALLA_URL)
    try:
        candidates = await provider.route(
            RouteRequest(
                locations=(CLIFDEN, LETTERFRACK),
                preferences=CostingPreferences(),
            )
        )
    finally:
        await provider.aclose()

    assert candidates
    best = candidates[0]
    assert best.distance_m > 0
    coordinates = best.coordinates
    assert len(coordinates) >= 2
    # Decoded at the right precision the shape stays in Connemara; at the wrong
    # one it would land far outside these bounds.
    assert all(53.0 < point.lat < 54.0 for point in coordinates)
    assert all(-10.5 < point.lon < -9.5 for point in coordinates)


@requires_service
@requires_tiles
async def test_height_preserves_gaps_as_none() -> None:
    """Missing elevation must arrive as None, never as a substituted number."""
    provider = ValhallaProvider(VALHALLA_URL)
    try:
        samples = await provider.heights(
            (CLIFDEN.point, LETTERFRACK.point), with_range=True
        )
    finally:
        await provider.aclose()

    assert len(samples) == 2
    for distance_m, elevation_m in samples:
        assert distance_m >= 0
        assert elevation_m is None or isinstance(elevation_m, float)
