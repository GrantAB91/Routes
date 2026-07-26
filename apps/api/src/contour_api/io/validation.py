"""Upload validation for route files.

Every check here runs *before* any parser sees the bytes (§15.3, §20.5). Route
files arrive from strangers, and the libraries that read them are written for
correctness on well-formed input rather than for hostility.

The XML guard is the one that matters most. What follows was measured against
the versions actually installed (gpxpy 1.6.2 with lxml 5.x), not assumed:

* **External entities are not resolved.** ``<!ENTITY xxe SYSTEM "file:///...">``
  makes lxml raise "Entity 'xxe' not defined" rather than reading the file. The
  classic XXE file-disclosure attack does not work against this configuration.
* **Internal entity expansion does happen.** A nested definition expanded to
  1,000 characters in testing, which is the exponential-expansion vector
  ("billion laughs"). This one is live.
* **The stdlib fallback parses DOCTYPE happily.** gpxpy prefers lxml but falls
  back to ``xml.etree.ElementTree`` when it is absent, so the protection above
  is a property of an optional dependency rather than of Contour.

So the entity guard earns its place on the expansion vector today, and the
DOCTYPE guard is defence in depth against the parser backend changing under us.
A legitimate GPX, TCX or KML file never needs either construct, so both are
refused outright rather than sanitised — there is no valid use to preserve.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

# Generous enough for a multi-day tour recorded at one point per second, small
# enough that a single upload cannot exhaust a worker.
MAX_UPLOAD_BYTES = 64 * 1024 * 1024
MAX_POINTS = 2_000_000
# Deeply nested XML exhausts the parser stack even without entity expansion.
MAX_XML_DEPTH = 100


class FileFormat(StrEnum):
    GPX = "gpx"
    TCX = "tcx"
    KML = "kml"
    GEOJSON = "geojson"
    FIT = "fit"
    UNKNOWN = "unknown"


class RejectionCode(StrEnum):
    EMPTY = "empty_file"
    TOO_LARGE = "file_too_large"
    UNKNOWN_FORMAT = "unrecognised_format"
    XML_DOCTYPE = "xml_doctype_forbidden"
    XML_ENTITY = "xml_entity_forbidden"
    XML_TOO_DEEP = "xml_nesting_too_deep"
    MALFORMED = "malformed_file"
    NO_GEOMETRY = "no_geometry_found"
    COORDINATES_OUT_OF_RANGE = "coordinates_out_of_range"
    TOO_MANY_POINTS = "too_many_points"


class FileRejectedError(Exception):
    """A file that will not be processed, with a reason a user can act on.

    Carries a code so the API never has to fall back to a generic failure
    message (§16.7).
    """

    def __init__(self, code: RejectionCode, message: str) -> None:
        self.code = code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class DetectedFile:
    format: FileFormat
    size_bytes: int
    # What the client claimed, kept for the audit trail. Never trusted.
    declared_content_type: str | None = None
    declared_filename: str | None = None


# Matched on the raw bytes before decoding, so an encoding trick cannot hide a
# declaration from the check.
_DOCTYPE = re.compile(rb"<!DOCTYPE", re.IGNORECASE)
_ENTITY = re.compile(rb"<!ENTITY", re.IGNORECASE)


def detect_format(data: bytes, filename: str | None = None) -> FileFormat:
    """Identify a file by its content.

    The extension and the client-supplied content type are not consulted. Both
    are attacker-controlled, and a file named ``route.gpx`` containing JSON is
    far more likely to be a mistake or a probe than a format Contour should
    guess at.
    """
    head = data[:4096].lstrip()

    # FIT files begin with a header whose bytes 8-12 are the ASCII ".FIT".
    if len(data) >= 12 and data[8:12] == b".FIT":
        return FileFormat.FIT

    if head.startswith(b"{") or head.startswith(b"["):
        return FileFormat.GEOJSON

    lowered = head.lower()
    if b"<gpx" in lowered:
        return FileFormat.GPX
    if b"<trainingcenterdatabase" in lowered:
        return FileFormat.TCX
    if b"<kml" in lowered:
        return FileFormat.KML

    return FileFormat.UNKNOWN


def guard_xml(data: bytes) -> None:
    """Refuse XML that could read the filesystem or exhaust memory.

    Rejecting rather than stripping is deliberate. A stripped DOCTYPE changes
    the document the user uploaded, and silently altering someone's file to make
    it safe is worse than telling them it was refused.
    """
    if _DOCTYPE.search(data):
        raise FileRejectedError(
            RejectionCode.XML_DOCTYPE,
            "This file contains a document type declaration. Route files never "
            "need one, and depending on the XML parser it can be used to read "
            "server files or exhaust memory, so the upload was refused. Export "
            "the route again from your device or software.",
        )
    if _ENTITY.search(data):
        raise FileRejectedError(
            RejectionCode.XML_ENTITY,
            "This file defines XML entities. Route files do not need them, and "
            "they can be used to exhaust server memory, so the upload was refused.",
        )

    depth = 0
    deepest = 0
    for match in re.finditer(rb"<(/?)([A-Za-z_][\w.:-]*)([^>]*?)(/?)>", data):
        closing, _, _, self_closing = match.groups()
        if closing:
            depth = max(0, depth - 1)
        elif not self_closing:
            depth += 1
            deepest = max(deepest, depth)
            if deepest > MAX_XML_DEPTH:
                raise FileRejectedError(
                    RejectionCode.XML_TOO_DEEP,
                    f"This file nests XML elements more than {MAX_XML_DEPTH} deep, "
                    "which is not valid for a route file.",
                )


def validate_upload(
    data: bytes,
    filename: str | None = None,
    declared_content_type: str | None = None,
) -> DetectedFile:
    """Run every pre-parse check and report what the file is."""
    if not data:
        raise FileRejectedError(RejectionCode.EMPTY, "The uploaded file is empty.")

    if len(data) > MAX_UPLOAD_BYTES:
        raise FileRejectedError(
            RejectionCode.TOO_LARGE,
            f"This file is {len(data) / 1024 / 1024:.0f} MB. The limit is "
            f"{MAX_UPLOAD_BYTES // 1024 // 1024} MB.",
        )

    detected = detect_format(data, filename)
    if detected is FileFormat.UNKNOWN:
        raise FileRejectedError(
            RejectionCode.UNKNOWN_FORMAT,
            "This file is not a GPX, TCX, KML, GeoJSON or FIT route file. Contour "
            "identifies files by their content, so renaming a file does not change "
            "how it is read.",
        )

    if detected in {FileFormat.GPX, FileFormat.TCX, FileFormat.KML}:
        guard_xml(data)

    return DetectedFile(
        format=detected,
        size_bytes=len(data),
        declared_content_type=declared_content_type,
        declared_filename=filename,
    )


def validate_coordinates(points: list[tuple[float, float]]) -> None:
    """Reject geometry outside the possible range of coordinates.

    Out-of-range values usually mean the file was written with latitude and
    longitude swapped, or decoded at the wrong precision. Either way the route
    is not where the file claims, and importing it would place a journey in the
    wrong hemisphere rather than fail visibly.
    """
    if not points:
        raise FileRejectedError(
            RejectionCode.NO_GEOMETRY,
            "No route geometry was found in this file.",
        )
    if len(points) > MAX_POINTS:
        raise FileRejectedError(
            RejectionCode.TOO_MANY_POINTS,
            f"This file contains {len(points):,} points. The limit is {MAX_POINTS:,}.",
        )

    for index, (lat, lon) in enumerate(points):
        if not -90.0 <= lat <= 90.0 or not -180.0 <= lon <= 180.0:
            raise FileRejectedError(
                RejectionCode.COORDINATES_OUT_OF_RANGE,
                f"Point {index + 1} is at {lat}, {lon}, which is not a valid "
                "coordinate. This often means latitude and longitude are swapped "
                "in the file.",
            )
