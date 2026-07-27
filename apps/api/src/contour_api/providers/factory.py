"""Constructing providers from configuration, in one place.

Every provider Contour talks to has a disabled state, and that state is a real
answer rather than an error to be smoothed over (§21.8, §25.18). Centralising
construction means the disabled state is described once, with its remedy, and
every endpoint reports it the same way instead of each inventing its own
fallback.

Nothing here substitutes a default provider for a missing one. If elevation is
not configured, routes are produced without elevation and say so; they do not
quietly acquire a profile from somewhere else.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from ..config import (
    DisabledCapabilityError,
    ElevationProviderName,
    Settings,
    get_settings,
)
from .elevation import ElevationProvider, RasterElevationProvider
from .valhalla import ValhallaProvider


def routing_provider(settings: Settings | None = None) -> ValhallaProvider:
    """The routing engine, or a :class:`DisabledCapabilityError` naming the fix."""
    settings = settings or get_settings()
    return ValhallaProvider(settings.require_valhalla())


def elevation_provider(settings: Settings | None = None) -> ElevationProvider:
    """The elevation source, or a :class:`DisabledCapabilityError` naming the fix.

    The raster provider holds open file handles per tile, so it is cached: a
    fresh instance per request would re-open every GeoTIFF in the directory on
    each route.
    """
    settings = settings or get_settings()
    name = settings.require_elevation()

    if name is ElevationProviderName.RASTER:
        return _raster(settings.elevation_raster_dir, settings.elevation_dataset_id or "")

    if name is ElevationProviderName.VALHALLA:
        # Valhalla serves elevation through the same service as routing, but
        # only where elevation tiles were built alongside the routing tiles.
        return _ValhallaElevation(routing_provider(settings))

    raise DisabledCapabilityError(
        capability="elevation",
        reason=f"elevation provider '{name}' is not implemented",
        remedy="Set CONTOUR_ELEVATION_PROVIDER to 'raster' or 'valhalla'.",
    )


@lru_cache(maxsize=4)
def _raster(directory: str, dataset_id: str) -> RasterElevationProvider:
    return RasterElevationProvider(Path(directory), dataset_id=dataset_id)


class _ValhallaElevation:
    """Adapts the routing provider's ``/height`` endpoint to ElevationProvider.

    ``resolution_m`` returns ``None`` deliberately. Valhalla's ``/height`` does
    not report which dataset answered or at what spacing, and the analysis layer
    treats an unknown resolution as an assumption it must state rather than as
    permission to sample as finely as it likes (§11.3).
    """

    name = "valhalla-height"

    def __init__(self, provider: ValhallaProvider) -> None:
        self._provider = provider

    async def health(self):
        return await self._provider.health()

    async def sample(self, points):  # pragma: no cover - requires a live engine
        pairs = await self._provider.heights(tuple(points), with_range=False)
        return [elevation for _, elevation in pairs]

    def resolution_m(self, at) -> float | None:
        return None
