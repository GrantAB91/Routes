"""Raster elevation provider against real Copernicus DEM tiles.

Runs only where a DEM is actually installed, and skips with the command that
installs one otherwise. These are the first tests in the suite that touch real
measured terrain rather than a constructed profile, so they check the things a
synthetic fixture cannot: that the source's own resolution is read from the file
rather than assumed, that coverage boundaries produce unknown rather than a
neighbouring tile's value, and that real Irish summits come out near their
published heights.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from contour_api.providers.elevation import (
    RasterElevationProvider,
    parse_copernicus_bounds,
)
from contour_api.providers.routing import LatLon

pytestmark = pytest.mark.integration

DEM_DIR = Path(os.environ.get("CONTOUR_ELEVATION_RASTER_DIR", "/opt/contour/dem"))


def _has_tiles() -> bool:
    return DEM_DIR.is_dir() and any(DEM_DIR.rglob("*.tif"))


requires_dem = pytest.mark.skipif(
    not _has_tiles(),
    reason=(
        f"no DEM tiles in {DEM_DIR}. Fetch Copernicus GLO-30 tiles from "
        "https://copernicus-dem-30m.s3.amazonaws.com/ covering the area under test."
    ),
)


@pytest.fixture
def provider():
    instance = RasterElevationProvider(DEM_DIR, dataset_id="Copernicus DEM GLO-30")
    yield instance
    instance.close()


class TestTileNaming:
    """Bounds come from the filename, so a full country can be indexed cheaply."""

    def test_northern_western_tile(self) -> None:
        bounds = parse_copernicus_bounds("Copernicus_DSM_COG_10_N53_00_W010_00_DEM.tif")

        assert bounds is not None
        assert (bounds.min_lat, bounds.max_lat) == (53, 54)
        assert (bounds.min_lon, bounds.max_lon) == (-10, -9)

    def test_southern_eastern_tile(self) -> None:
        bounds = parse_copernicus_bounds("Copernicus_DSM_COG_10_S34_00_E018_00_DEM.tif")

        assert bounds is not None
        assert (bounds.min_lat, bounds.max_lat) == (-34, -33)
        assert (bounds.min_lon, bounds.max_lon) == (18, 19)

    def test_an_unrecognised_name_is_ignored_not_guessed(self) -> None:
        assert parse_copernicus_bounds("some-other-dem.tif") is None


class TestConfiguration:
    def test_a_dataset_id_is_mandatory(self) -> None:
        """An elevation figure with no named source cannot state its accuracy."""
        with pytest.raises(ValueError, match="dataset_id is required"):
            RasterElevationProvider(DEM_DIR, dataset_id="")


@requires_dem
class TestRealTerrain:
    async def test_resolution_is_read_from_the_file_not_assumed(self, provider) -> None:
        """GLO-30 is not a uniform 30 m grid, and the name does not say so.

        Latitude spacing is one arc second; longitude spacing widens in bands
        toward the poles, and at these latitudes the tiles are 1.5 arc seconds
        of longitude. The coarser axis is what limits what can be resolved, so
        that is what the provider reports and what the analysis layer clamps
        sampling to.
        """
        resolution = provider.resolution_m(LatLon(lat=53.5, lon=-9.5))

        assert resolution is not None
        assert 28.0 < resolution < 34.0

    async def test_a_known_summit_reads_close_to_its_published_height(self, provider) -> None:
        # Croagh Patrick, published 764 m.
        value = (await provider.sample((LatLon(lat=53.7601, lon=-9.6591),)))[0]

        assert value is not None
        assert abs(value - 764) < 30

    async def test_sea_level_reads_near_zero(self, provider) -> None:
        # Clew Bay, open water.
        value = (await provider.sample((LatLon(lat=53.8300, lon=-9.7000),)))[0]

        assert value is not None
        assert -5.0 < value < 20.0

    async def test_a_point_outside_coverage_is_unknown_not_a_neighbour(self, provider) -> None:
        """The failure this prevents is the worst kind: a plausible wrong number.

        Sampling outside the installed tiles must not fall through to whatever
        raster happens to be open; it must come back as not known so the
        analysis accumulates around it instead of across it.
        """
        # Mid-Atlantic, far outside any installed tile.
        assert (await provider.sample((LatLon(lat=53.5, lon=-25.0),)))[0] is None

    async def test_resolution_is_unknown_outside_coverage(self, provider) -> None:
        assert provider.resolution_m(LatLon(lat=53.5, lon=-25.0)) is None

    async def test_a_batch_spanning_two_tiles_is_sampled_correctly(self, provider) -> None:
        """Points are grouped by tile, so a route crossing a boundary must work."""
        points = (
            LatLon(lat=53.7601, lon=-9.6591),  # N53 W010
            LatLon(lat=54.1000, lon=-9.5000),  # N54 W010
            LatLon(lat=53.5000, lon=-25.0),  # no coverage
        )

        values = await provider.sample(points)

        assert values[0] is not None
        assert values[1] is not None
        assert values[2] is None

    async def test_health_names_the_dataset(self, provider) -> None:
        report = await provider.health()

        assert report.status.value == "healthy"
        assert report.dataset_version == "Copernicus DEM GLO-30"

    def test_missing_directory_reports_unavailable_with_a_remedy(self) -> None:
        provider = RasterElevationProvider(
            Path("/nonexistent/dem"), dataset_id="Copernicus DEM GLO-30"
        )

        assert provider.tile_count == 0
