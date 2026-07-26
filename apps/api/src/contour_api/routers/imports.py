"""File import (§15.1-15.5).

Import preserves the uploaded file and shows the user what was and was not in
it before anything is derived from it. Map matching, where offered, produces a
*second* geometry rather than replacing the first, so the rider can see the
difference and keep whichever they prefer (§15.5.9).
"""

from __future__ import annotations

import hashlib
from typing import Annotated, Any

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from ..io.gpx import parse_gpx
from ..io.validation import (
    MAX_UPLOAD_BYTES,
    FileFormat,
    FileRejectedError,
    RejectionCode,
    validate_upload,
)

router = APIRouter(prefix="/v1/imports", tags=["import"])


class ImportedPoint(BaseModel):
    lat: float
    lon: float
    elevation_m: float | None = Field(
        default=None,
        description="Null means the file did not record elevation here. Never zero-filled.",
    )


class ImportPreview(BaseModel):
    filename: str
    detected_format: str
    size_bytes: int
    checksum_sha256: str
    name: str | None
    point_count: int
    has_elevation: bool
    has_timestamps: bool
    missing: list[str] = Field(
        description="What the file did not contain, shown before anything is derived from it."
    )
    bounds: dict[str, float] | None
    first_points: list[ImportedPoint]
    waypoint_count: int
    source_metadata: dict[str, Any]
    notes: list[str]


@router.post(
    "",
    response_model=ImportPreview,
    summary="Validate and preview an uploaded route file",
    description=(
        "Runs every pre-parse check, then reports what the file contains and what "
        "it does not. The original is preserved unchanged; nothing is derived "
        "until the user confirms."
    ),
)
async def create_import(file: Annotated[UploadFile, File()]) -> ImportPreview:
    data = await file.read()

    # Raises FileRejectedError, which the error handler turns into a 422 with a
    # stable code and a sentence the user can act on.
    detected = validate_upload(data, file.filename, file.content_type)

    if detected.format is not FileFormat.GPX:
        raise FileRejectedError(
            RejectionCode.UNKNOWN_FORMAT,
            f"{detected.format.value.upper()} files are recognised but not yet "
            "imported. GPX is supported today; TCX, KML, GeoJSON and FIT readers "
            "are not implemented.",
        )

    track = parse_gpx(data)

    lats = [point.lat for point in track.points]
    lons = [point.lon for point in track.points]
    bounds = (
        {
            "min_lat": min(lats),
            "max_lat": max(lats),
            "min_lon": min(lons),
            "max_lon": max(lons),
        }
        if lats
        else None
    )

    notes: list[str] = []
    if not track.has_elevation:
        notes.append(
            "This file records no elevation. Contour will report ascent as unknown "
            "unless an elevation provider is configured to sample it."
        )
    if not track.has_time:
        notes.append("This file records no timestamps.")
    notes.append(
        "The original file is preserved unchanged. Any map-matched geometry is "
        "stored alongside it, never in place of it."
    )

    return ImportPreview(
        filename=file.filename or "upload",
        detected_format=detected.format.value,
        size_bytes=detected.size_bytes,
        checksum_sha256=hashlib.sha256(data).hexdigest(),
        name=track.name,
        point_count=len(track.points),
        has_elevation=track.has_elevation,
        has_timestamps=track.has_time,
        missing=track.missing,
        bounds=bounds,
        first_points=[
            ImportedPoint(lat=p.lat, lon=p.lon, elevation_m=p.elevation_m) for p in track.points[:5]
        ],
        waypoint_count=len(track.waypoints),
        source_metadata=track.metadata,
        notes=notes,
    )


@router.get("/limits", summary="Upload limits and supported formats")
async def limits() -> dict[str, Any]:
    return {
        "max_upload_bytes": MAX_UPLOAD_BYTES,
        "formats": {
            "gpx": {"read": True, "write": True},
            # Stated rather than implied by omission (§24.3).
            "tcx": {"read": False, "write": False, "reason": "reader not implemented"},
            "kml": {"read": False, "write": False, "reason": "reader not implemented"},
            "geojson": {"read": False, "write": False, "reason": "reader not implemented"},
            "fit": {
                "read": False,
                "write": False,
                "reason": "reader not implemented; needs a verified specification and library check",
            },
        },
        "rejections": [code.value for code in RejectionCode],
    }
