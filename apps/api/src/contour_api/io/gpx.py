"""GPX reading and writing.

Contour treats GPX as a lossy container it does not control. Files arrive from
head units, phones, and a decade of software with varying ideas about the
schema, so the reader takes what is present and records what is missing rather
than filling gaps.

Two rules shape the round trip required by §15.7:

* elevation and time are preserved when present and left absent when not. A GPX
  point with no ``<ele>`` is written back without one, because writing a zero
  would turn "not recorded" into "at sea level";
* the original file is never modified. Import stores the upload untouched and
  writes any derived geometry alongside it (§15.2, §6.8).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import gpxpy
import gpxpy.gpx

from .validation import FileRejectedError, RejectionCode, validate_coordinates

CREATOR = "Contour"


@dataclass(frozen=True, slots=True)
class TrackPoint:
    lat: float
    lon: float
    # Absent means not recorded. Never defaulted to zero.
    elevation_m: float | None = None
    time: datetime | None = None


@dataclass
class ParsedTrack:
    name: str | None = None
    description: str | None = None
    points: list[TrackPoint] = field(default_factory=list)
    # Named waypoints, which are separate from the track line in GPX and are
    # commonly where a rider's actual stops live.
    waypoints: list[TrackPoint] = field(default_factory=list)
    waypoint_names: list[str] = field(default_factory=list)
    # What the file could not tell us, surfaced on the import screen (§15.5.8).
    missing: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def has_elevation(self) -> bool:
        return any(point.elevation_m is not None for point in self.points)

    @property
    def has_time(self) -> bool:
        return any(point.time is not None for point in self.points)


def parse_gpx(data: bytes) -> ParsedTrack:
    """Read a GPX file into a track.

    Callers must have run :func:`contour_api.io.validation.validate_upload`
    first. This function does no XML hardening of its own, and gpxpy passes the
    document straight to whichever backend is installed — which, for the
    standard-library fallback, accepts a DOCTYPE without complaint.
    """
    try:
        gpx = gpxpy.parse(data.decode("utf-8-sig", errors="strict"))
    except UnicodeDecodeError as exc:
        raise FileRejectedError(
            RejectionCode.MALFORMED,
            "This file is not valid UTF-8 text, so it could not be read as GPX.",
        ) from exc
    except Exception as exc:  # gpxpy raises several unrelated exception types
        raise FileRejectedError(
            RejectionCode.MALFORMED, f"This GPX file could not be read: {exc}"
        ) from exc

    points: list[TrackPoint] = []
    names: list[str] = []

    for track in gpx.tracks:
        if track.name:
            names.append(track.name)
        for segment in track.segments:
            for point in segment.points:
                points.append(
                    TrackPoint(
                        lat=point.latitude,
                        lon=point.longitude,
                        elevation_m=point.elevation,
                        time=point.time,
                    )
                )

    # Routes (<rte>) are planned lines rather than recordings. A file with no
    # tracks but a route is a perfectly ordinary export from planning software.
    if not points:
        for route in gpx.routes:
            if route.name:
                names.append(route.name)
            for point in route.points:
                points.append(
                    TrackPoint(
                        lat=point.latitude,
                        lon=point.longitude,
                        elevation_m=point.elevation,
                        time=point.time,
                    )
                )

    validate_coordinates([(p.lat, p.lon) for p in points])

    waypoints = [
        TrackPoint(lat=w.latitude, lon=w.longitude, elevation_m=w.elevation, time=w.time)
        for w in gpx.waypoints
    ]

    parsed = ParsedTrack(
        name=names[0] if names else (gpx.name or None),
        description=gpx.description,
        points=points,
        waypoints=waypoints,
        waypoint_names=[w.name or "" for w in gpx.waypoints],
        metadata={
            "creator": gpx.creator,
            "version": gpx.version,
            "track_count": len(gpx.tracks),
            "route_count": len(gpx.routes),
        },
    )

    if not parsed.has_elevation:
        parsed.missing.append("elevation")
    if not parsed.has_time:
        parsed.missing.append("timestamps")
    if not parsed.name:
        parsed.missing.append("name")

    return parsed


def write_gpx(
    track: ParsedTrack,
    *,
    creator: str = CREATOR,
    as_route: bool = False,
) -> str:
    """Write a track back to GPX.

    ``as_route`` writes a ``<rte>`` rather than a ``<trk>``. A route Contour
    generated was never ridden, and presenting it as a recorded track would
    misrepresent it to whatever reads the file next.
    """
    gpx = gpxpy.gpx.GPX()
    gpx.creator = creator
    gpx.name = track.name
    gpx.description = track.description

    if as_route:
        route = gpxpy.gpx.GPXRoute(name=track.name)
        for point in track.points:
            route.points.append(
                gpxpy.gpx.GPXRoutePoint(
                    latitude=point.lat,
                    longitude=point.lon,
                    elevation=point.elevation_m,
                    time=point.time,
                )
            )
        gpx.routes.append(route)
    else:
        gpx_track = gpxpy.gpx.GPXTrack(name=track.name)
        segment = gpxpy.gpx.GPXTrackSegment()
        for point in track.points:
            segment.points.append(
                gpxpy.gpx.GPXTrackPoint(
                    latitude=point.lat,
                    longitude=point.lon,
                    elevation=point.elevation_m,
                    time=point.time,
                )
            )
        gpx_track.segments.append(segment)
        gpx.tracks.append(gpx_track)

    for point, name in zip(
        track.waypoints,
        track.waypoint_names or [""] * len(track.waypoints),
        strict=False,
    ):
        gpx.waypoints.append(
            gpxpy.gpx.GPXWaypoint(
                latitude=point.lat,
                longitude=point.lon,
                elevation=point.elevation_m,
                time=point.time,
                name=name or None,
            )
        )

    return gpx.to_xml()
