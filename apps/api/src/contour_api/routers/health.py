"""Health and capability reporting (§21.7, §23.4).

Two endpoints with deliberately different jobs:

``/health``
    Is this process alive? Cheap, no dependencies, suitable for a load balancer.

``/health/components``
    What can Contour actually do right now? Checks every dependency and reports
    each one's real state. A component that is switched off is reported as
    ``disabled`` with the setting that would enable it — distinct from
    ``unavailable``, which means it is configured but not answering.

The distinction matters operationally. "Elevation is disabled because no
provider is set" is a deployment decision; "elevation is unavailable" is an
incident. Collapsing them wakes someone at 3am for a working system.
"""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Response, status
from sqlalchemy import text

from ..config import DisabledCapabilityError, ElevationProviderName, get_settings
from ..db import get_engine
from ..providers.routing import ProviderHealth
from ..providers.valhalla import ValhallaProvider

router = APIRouter(tags=["health"])


@router.get("/health", summary="Liveness")
async def health() -> dict[str, str]:
    return {"status": "ok"}


async def _database() -> dict[str, Any]:
    try:
        async with get_engine().connect() as connection:
            await connection.execute(text("SELECT 1"))
            postgis = await connection.execute(text("SELECT postgis_version()"))
            version = postgis.scalar_one_or_none()
        return {
            "status": "healthy",
            "detail": "PostGIS reachable",
            "version": str(version) if version else None,
        }
    except Exception as exc:
        return {"status": "unavailable", "detail": f"database not reachable: {exc}"}


async def _routing() -> dict[str, Any]:
    settings = get_settings()
    try:
        url = settings.require_valhalla()
    except DisabledCapabilityError as exc:
        return {"status": "disabled", "detail": exc.reason, "remedy": exc.remedy}

    provider = ValhallaProvider(url)
    try:
        report = await provider.health()
    finally:
        await provider.aclose()

    payload: dict[str, Any] = {
        "status": report.status.value,
        "detail": report.detail,
        "version": report.version,
    }

    if report.status is ProviderHealth.HEALTHY:
        # A live engine with no tiles answers /status perfectly while being
        # unable to route a single metre. Reporting it as healthy would be the
        # most misleading green light in the system.
        tiles = _tile_count(settings.valhalla_tile_dir)
        payload["tile_count"] = tiles
        if tiles == 0:
            payload["status"] = "degraded"
            payload["detail"] = (
                "Valhalla is running but no routing tiles are built, so no route can be generated."
            )
            payload["remedy"] = (
                "Run infra/valhalla/build-tiles.sh, which needs an OpenStreetMap "
                "extract for the area you want to route in."
            )
    return payload


def _tile_count(tile_dir: str) -> int:
    """How many graph tiles are actually built.

    Valhalla either writes loose ``.gph`` files or packs them into a tar
    extract. Counting the tar as one tile — which this did — reports "1" for a
    283-tile Ireland build, and a reader seeing that would reasonably conclude
    the build had barely produced anything. The archive is opened and its
    members counted so the number means what it says.
    """
    path = Path(tile_dir)
    if not path.is_dir():
        return 0

    archive = path / "tiles.tar"
    if archive.exists():
        try:
            with tarfile.open(archive) as tar:
                return sum(1 for name in tar.getnames() if name.endswith(".gph"))
        except (tarfile.TarError, OSError):
            # A tar that cannot be read is not evidence of tiles. Falling back
            # to the loose count says what is genuinely on disk.
            pass

    return sum(1 for _ in path.rglob("*.gph"))


def _elevation() -> dict[str, Any]:
    settings = get_settings()
    try:
        provider = settings.require_elevation()
    except DisabledCapabilityError as exc:
        return {"status": "disabled", "detail": exc.reason, "remedy": exc.remedy}

    if provider is ElevationProviderName.RASTER:
        path = Path(settings.elevation_raster_dir)
        rasters = (
            sum(1 for _ in path.rglob("*.tif")) + sum(1 for _ in path.rglob("*.hgt"))
            if path.is_dir()
            else 0
        )
        if rasters == 0:
            return {
                "status": "unavailable",
                "detail": f"no DEM files found in {settings.elevation_raster_dir}",
                "remedy": "Place GeoTIFF or HGT tiles in that directory.",
            }
        return {
            "status": "healthy",
            "detail": f"{rasters} DEM file(s) available",
            "dataset": settings.elevation_dataset_id,
        }

    return {
        "status": "healthy",
        "detail": "elevation served by Valhalla /height",
        "dataset": settings.elevation_dataset_id,
    }


def _object_storage() -> dict[str, Any]:
    settings = get_settings()
    if not settings.s3_bucket:
        return {
            "status": "disabled",
            "detail": "no object storage configured",
            "remedy": (
                "Set CONTOUR_S3_ENDPOINT and CONTOUR_S3_BUCKET to archive source "
                "responses and store exports. Without it, imports are processed but "
                "the original file is not retained."
            ),
        }
    return {"status": "healthy", "detail": f"bucket {settings.s3_bucket}"}


def _disk() -> dict[str, Any]:
    usage = shutil.disk_usage("/")
    free_ratio = usage.free / usage.total
    return {
        "status": "healthy" if free_ratio > 0.05 else "degraded",
        "detail": f"{usage.free / 1e9:.1f} GB free of {usage.total / 1e9:.1f} GB",
    }


@router.get(
    "/health/components",
    summary="Capability report",
    description=(
        "Reports each dependency's real state. 'disabled' means switched off by "
        "configuration and carries the remedy; 'unavailable' means configured but "
        "not responding."
    ),
)
async def components(response: Response) -> dict[str, Any]:
    checks = {
        "database": await _database(),
        "routing": await _routing(),
        "elevation": _elevation(),
        "object_storage": _object_storage(),
        "disk": _disk(),
    }

    # Only a component that is configured-but-broken makes the system unhealthy.
    # A deliberately disabled capability is a limited deployment, not an outage.
    unavailable = [name for name, check in checks.items() if check["status"] == "unavailable"]
    degraded = [name for name, check in checks.items() if check["status"] == "degraded"]

    if unavailable:
        overall = "unavailable"
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    elif degraded:
        overall = "degraded"
    else:
        overall = "healthy"

    return {
        "status": overall,
        "components": checks,
        "unavailable": unavailable,
        "degraded": degraded,
    }
