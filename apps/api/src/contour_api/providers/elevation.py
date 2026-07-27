"""Elevation providers (§5.6).

Two implementations behind one seam:

* :class:`RasterElevationProvider` reads local DEM tiles directly. This is the
  one Contour uses when a DEM is installed, because it can report the source's
  *actual* resolution at the sampled latitude rather than assuming one.
* :class:`ValhallaElevationProvider` uses the routing engine's ``/height``
  endpoint, for deployments where elevation is built into the routing tiles.

Both return ``None`` for any sample they cannot serve. Nothing here substitutes
a value: a point outside coverage, a nodata cell, or a failed request all come
back as "not known", and :mod:`contour_api.analysis.elevation` accumulates
around the gap rather than bridging it.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .routing import HealthReport, LatLon, ProviderHealth

# Copernicus GLO-30 tiles are named, for example:
#   Copernicus_DSM_COG_10_N53_00_W010_00_DEM.tif
# The "10" is the resolution in arc seconds, not metres.
_COP_TILE = re.compile(
    r"Copernicus_DSM_COG_\d+_([NS])(\d{2})_00_([EW])(\d{3})_00_DEM", re.IGNORECASE
)

# One arc second of latitude, near enough constant everywhere.
_METRES_PER_ARCSEC_LAT = 30.87


class ElevationProvider(Protocol):
    name: str

    async def health(self) -> HealthReport: ...

    async def sample(self, points: tuple[LatLon, ...]) -> list[float | None]:
        """Elevation in metres per point, ``None`` where not known.

        Asynchronous because a provider may reach the network to answer. The
        raster implementation reads local files and does no awaiting; the shape
        is the protocol's, so a network-backed source can satisfy it without
        every caller having to know which kind it holds.
        """
        ...

    def resolution_m(self, at: LatLon) -> float | None:
        """Native horizontal resolution at a location, for §11.3.

        The analysis layer uses this to refuse to sample more finely than the
        source supports. Returning ``None`` means the provider cannot say, and
        the caller must then treat its own configured value as an assumption.
        """
        ...


@dataclass(frozen=True, slots=True)
class TileBounds:
    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, point: LatLon) -> bool:
        return self.min_lat <= point.lat < self.max_lat and self.min_lon <= point.lon < self.max_lon


def parse_copernicus_bounds(filename: str) -> TileBounds | None:
    """Derive a tile's one-degree square extent from its Copernicus filename.

    Reading bounds from the name rather than opening every file lets the index
    be built without touching the rasters, which matters when a full country is
    installed and only a handful of tiles are on any given route.
    """
    match = _COP_TILE.search(filename)
    if not match:
        return None
    ns, lat_s, ew, lon_s = match.groups()
    lat = int(lat_s) * (1 if ns.upper() == "N" else -1)
    lon = int(lon_s) * (1 if ew.upper() == "E" else -1)
    return TileBounds(min_lat=lat, max_lat=lat + 1, min_lon=lon, max_lon=lon + 1)


@dataclass
class RasterElevationProvider:
    """Samples elevation from local GeoTIFF or HGT tiles.

    ``dataset_id`` is mandatory and is recorded against every sample. An
    elevation figure whose source cannot be named cannot have its accuracy
    stated, so the provider refuses to be constructed without it.
    """

    directory: Path
    dataset_id: str
    name: str = "raster"

    def __post_init__(self) -> None:
        if not self.dataset_id:
            raise ValueError(
                "dataset_id is required: an elevation figure with no named source "
                "cannot have its accuracy or attribution stated"
            )
        self.directory = Path(self.directory)
        self._index: list[tuple[TileBounds, Path]] = []
        self._open: dict[Path, object] = {}
        self._build_index()

    def _build_index(self) -> None:
        if not self.directory.is_dir():
            return
        for path in sorted(self.directory.rglob("*.tif")):
            bounds = parse_copernicus_bounds(path.name)
            if bounds is not None:
                self._index.append((bounds, path))

    @property
    def tile_count(self) -> int:
        return len(self._index)

    def _tile_for(self, point: LatLon) -> Path | None:
        for bounds, path in self._index:
            if bounds.contains(point):
                return path
        return None

    def _dataset(self, path: Path):
        # rasterio is an optional dependency (the "raster" extra), so it is
        # imported at use rather than at module import. A deployment using the
        # Valhalla provider should not need the whole GDAL stack installed.
        import rasterio

        if path not in self._open:
            self._open[path] = rasterio.open(path)
        return self._open[path]

    def close(self) -> None:
        for dataset in self._open.values():
            dataset.close()  # type: ignore[attr-defined]
        self._open.clear()

    async def health(self) -> HealthReport:
        if not self.directory.is_dir():
            return HealthReport(
                status=ProviderHealth.UNAVAILABLE,
                detail=f"DEM directory {self.directory} does not exist",
            )
        if not self._index:
            return HealthReport(
                status=ProviderHealth.UNAVAILABLE,
                detail=(
                    f"no recognised DEM tiles in {self.directory}. Copernicus tiles "
                    "are named Copernicus_DSM_COG_<res>_<N|S>NN_00_<E|W>NNN_00_DEM.tif"
                ),
            )
        return HealthReport(
            status=ProviderHealth.HEALTHY,
            detail=f"{len(self._index)} DEM tile(s) available",
            dataset_version=self.dataset_id,
        )

    def resolution_m(self, at: LatLon) -> float | None:
        """Ground resolution in metres at ``at``.

        GLO-30 is *not* a uniform 30 m grid. Latitude spacing is a constant one
        arc second, but longitude spacing widens in bands toward the poles — at
        53°N the tiles here are 1.5 arc seconds of longitude. Reporting a flat
        "30 m" would understate the cell size and let the analysis layer sample
        more finely than the data supports, which is the false precision §11.3
        exists to prevent.

        The coarser of the two axes is returned, since that is what actually
        limits what can be resolved.
        """
        tile = self._tile_for(at)
        if tile is None:
            return None

        dataset = self._dataset(tile)
        lon_deg, lat_deg = dataset.res  # type: ignore[attr-defined]

        lat_m = lat_deg * 3600.0 * _METRES_PER_ARCSEC_LAT
        lon_m = lon_deg * 3600.0 * _METRES_PER_ARCSEC_LAT * math.cos(math.radians(at.lat))
        return max(lat_m, lon_m)

    async def sample(self, points: tuple[LatLon, ...]) -> list[float | None]:
        """Sample every point, grouping by tile to avoid reopening files.

        Points outside coverage and nodata cells both return ``None``. They are
        different situations — no tile installed versus a hole in the data — but
        both mean the same thing to everything downstream: this elevation is not
        known, so do not compute across it.
        """
        results: list[float | None] = [None] * len(points)
        by_tile: dict[Path, list[int]] = {}

        for index, point in enumerate(points):
            tile = self._tile_for(point)
            if tile is not None:
                by_tile.setdefault(tile, []).append(index)

        for tile, indices in by_tile.items():
            dataset = self._dataset(tile)
            coordinates = [(points[i].lon, points[i].lat) for i in indices]
            nodata = dataset.nodata  # type: ignore[attr-defined]

            for index, value in zip(
                indices,
                dataset.sample(coordinates),
                strict=True,  # type: ignore[attr-defined]
            ):
                raw = float(value[0])
                if nodata is not None and raw == float(nodata):
                    continue
                # Copernicus uses a very negative sentinel in some products even
                # where nodata is unset in the header. No point on land is below
                # the Dead Sea shore, so anything under -500 m is not a
                # measurement.
                if raw < -500.0 or math.isnan(raw):
                    continue
                results[index] = raw

        return results


@dataclass
class ValhallaElevationProvider:
    """Samples elevation through Valhalla's ``/height`` endpoint."""

    provider: object  # ValhallaProvider; typed loosely to avoid a cycle
    dataset_id: str | None = None
    name: str = "valhalla"

    async def health(self) -> HealthReport:
        return await self.provider.health()  # type: ignore[attr-defined]

    def resolution_m(self, at: LatLon) -> float | None:
        # Valhalla does not report the resolution of the elevation built into
        # its tiles, so Contour cannot state one. The caller must treat its
        # configured value as an assumption rather than a measurement.
        return None

    def sample(self, points: tuple[LatLon, ...]) -> list[float | None]:
        raise NotImplementedError("ValhallaElevationProvider.sample is async; use sample_async")

    async def sample_async(self, points: tuple[LatLon, ...]) -> list[float | None]:
        pairs = await self.provider.heights(points, with_range=False)  # type: ignore[attr-defined]
        return [elevation for _, elevation in pairs]
