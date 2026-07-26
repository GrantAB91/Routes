"""Upload validation and GPX round-trip tests.

Two groups matter. The security group proves hostile files are refused before
any parser touches them; each test records what the installed stack actually
does, which is not always what the vulnerability's reputation suggests. The
round-trip group proves §15.7: geometry and metadata survive export and
reimport, and absent data stays absent rather than becoming zero.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from contour_api.io.gpx import ParsedTrack, TrackPoint, parse_gpx, write_gpx
from contour_api.io.validation import (
    FileFormat,
    FileRejectedError,
    RejectionCode,
    detect_format,
    validate_upload,
)

MINIMAL_GPX = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><name>Clifden to Letterfrack</name><trkseg>
    <trkpt lat="53.4890" lon="-10.0200"><ele>12.4</ele><time>2026-07-26T09:00:00Z</time></trkpt>
    <trkpt lat="53.5100" lon="-9.9800"><ele>38.1</ele><time>2026-07-26T09:12:00Z</time></trkpt>
    <trkpt lat="53.5497" lon="-9.9469"><ele>21.0</ele><time>2026-07-26T09:25:00Z</time></trkpt>
  </trkseg></trk>
</gpx>
"""

GPX_WITHOUT_ELEVATION = b"""<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">
  <trk><trkseg>
    <trkpt lat="53.4890" lon="-10.0200"/>
    <trkpt lat="53.5100" lon="-9.9800"/>
  </trkseg></trk>
</gpx>
"""


class TestSecurity:
    def test_xxe_doctype_is_refused(self) -> None:
        """External-entity declarations are refused before any parser runs.

        Measured behaviour of the installed stack: lxml does *not* resolve this
        entity, raising "Entity 'xxe' not defined" instead, so the file-read
        attack does not currently work. The guard stays because that protection
        comes from an optional dependency — gpxpy falls back to the standard
        library when lxml is absent, and that parser accepts DOCTYPE without
        complaint. Contour should not depend on which XML backend happens to be
        installed for whether it can be made to read local files.
        """
        hostile = b"""<?xml version="1.0"?>
        <!DOCTYPE gpx [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
        <gpx version="1.1"><trk><name>&xxe;</name></trk></gpx>
        """

        with pytest.raises(FileRejectedError) as caught:
            validate_upload(hostile, "route.gpx")

        assert caught.value.code is RejectionCode.XML_DOCTYPE

    def test_entity_expansion_is_refused(self) -> None:
        """Exponential entity expansion, the vector that is genuinely live.

        Unlike the external-entity case, this one is not blocked by the parser:
        feeding the definition below straight to gpxpy expands it to 1,000
        characters, and each additional level multiplies that by ten. Refusing
        the declaration is what stops it.
        """
        bomb = b"""<?xml version="1.0"?>
        <!DOCTYPE lolz [
          <!ENTITY lol "lol">
          <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
        ]>
        <gpx version="1.1"><trk><name>&lol2;</name></trk></gpx>
        """

        with pytest.raises(FileRejectedError) as caught:
            validate_upload(bomb, "route.gpx")

        assert caught.value.code in {RejectionCode.XML_DOCTYPE, RejectionCode.XML_ENTITY}

    def test_a_refused_file_is_not_silently_repaired(self) -> None:
        """Stripping the DOCTYPE would change the user's file behind their back."""
        hostile = b'<!DOCTYPE gpx><gpx version="1.1"><trk/></gpx>'

        with pytest.raises(FileRejectedError) as caught:
            validate_upload(hostile, "route.gpx")

        assert "refused" in str(caught.value)

    def test_deep_nesting_is_refused(self) -> None:
        deep = b"<gpx>" + b"<a>" * 200 + b"</a>" * 200 + b"</gpx>"

        with pytest.raises(FileRejectedError) as caught:
            validate_upload(deep, "route.gpx")

        assert caught.value.code is RejectionCode.XML_TOO_DEEP

    def test_oversized_upload_is_refused_before_parsing(self) -> None:
        with pytest.raises(FileRejectedError) as caught:
            validate_upload(b"<gpx>" + b"x" * (64 * 1024 * 1024 + 1), "route.gpx")

        assert caught.value.code is RejectionCode.TOO_LARGE

    def test_empty_upload_is_refused(self) -> None:
        with pytest.raises(FileRejectedError) as caught:
            validate_upload(b"", "route.gpx")

        assert caught.value.code is RejectionCode.EMPTY


class TestFormatDetection:
    def test_format_comes_from_content_not_the_name(self) -> None:
        """A renamed file must not be read as the format it claims to be."""
        assert detect_format(b'{"type":"FeatureCollection"}', "route.gpx") is (FileFormat.GEOJSON)
        assert detect_format(MINIMAL_GPX, "route.json") is FileFormat.GPX

    def test_fit_is_detected_by_its_header(self) -> None:
        fit = b"\x0e\x10\x43\x08\x00\x00\x00\x00.FIT\x00\x00"
        assert detect_format(fit, "activity.fit") is FileFormat.FIT

    def test_kml_and_tcx_are_distinguished(self) -> None:
        assert detect_format(b'<?xml version="1.0"?><kml xmlns="x"/>') is FileFormat.KML
        assert detect_format(b'<?xml version="1.0"?><TrainingCenterDatabase/>') is FileFormat.TCX

    def test_unrecognised_content_is_refused_with_an_explanation(self) -> None:
        with pytest.raises(FileRejectedError) as caught:
            validate_upload(b"just some text, not a route", "route.gpx")

        assert caught.value.code is RejectionCode.UNKNOWN_FORMAT
        assert "renaming a file does not change" in str(caught.value)


class TestParsing:
    def test_track_geometry_and_metadata_are_read(self) -> None:
        track = parse_gpx(MINIMAL_GPX)

        assert track.name == "Clifden to Letterfrack"
        assert len(track.points) == 3
        assert track.points[0].lat == pytest.approx(53.4890)
        assert track.points[1].elevation_m == pytest.approx(38.1)
        assert track.has_elevation
        assert track.has_time

    def test_absent_elevation_is_reported_not_zeroed(self) -> None:
        """A point with no <ele> must not become a point at sea level."""
        track = parse_gpx(GPX_WITHOUT_ELEVATION)

        assert not track.has_elevation
        assert all(point.elevation_m is None for point in track.points)
        assert "elevation" in track.missing

    def test_a_planned_route_element_is_read(self) -> None:
        """Planning software exports <rte>, not <trk>; both are valid input."""
        gpx = b"""<?xml version="1.0"?>
        <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
          <rte><name>Planned</name>
            <rtept lat="53.1" lon="-9.1"/><rtept lat="53.2" lon="-9.2"/>
          </rte>
        </gpx>
        """

        track = parse_gpx(gpx)

        assert track.name == "Planned"
        assert len(track.points) == 2

    def test_swapped_coordinates_are_refused(self) -> None:
        """Latitude beyond 90 means the file is wrong, not the world."""
        gpx = b"""<?xml version="1.0"?>
        <gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">
          <trk><trkseg><trkpt lat="-179.5" lon="53.2"/></trkseg></trk>
        </gpx>
        """

        with pytest.raises(FileRejectedError) as caught:
            parse_gpx(gpx)

        assert caught.value.code is RejectionCode.COORDINATES_OUT_OF_RANGE
        assert "swapped" in str(caught.value)

    def test_a_file_with_no_geometry_is_refused(self) -> None:
        gpx = b'<?xml version="1.0"?><gpx version="1.1"><metadata/></gpx>'

        with pytest.raises(FileRejectedError) as caught:
            parse_gpx(gpx)

        assert caught.value.code is RejectionCode.NO_GEOMETRY

    def test_malformed_xml_reports_a_usable_error(self) -> None:
        with pytest.raises(FileRejectedError) as caught:
            parse_gpx(b"<gpx><trk><trkseg></gpx>")

        assert caught.value.code is RejectionCode.MALFORMED


class TestRoundTrip:
    """§15.7: geometry and metadata survive export and reimport."""

    def test_geometry_survives_a_round_trip(self) -> None:
        original = parse_gpx(MINIMAL_GPX)

        reparsed = parse_gpx(write_gpx(original).encode())

        assert len(reparsed.points) == len(original.points)
        for before, after in zip(original.points, reparsed.points, strict=True):
            # GPX writes coordinates at full float precision, so the round trip
            # is exact rather than merely close.
            assert after.lat == pytest.approx(before.lat, abs=1e-9)
            assert after.lon == pytest.approx(before.lon, abs=1e-9)

    def test_elevation_and_time_survive_a_round_trip(self) -> None:
        original = parse_gpx(MINIMAL_GPX)

        reparsed = parse_gpx(write_gpx(original).encode())

        for before, after in zip(original.points, reparsed.points, strict=True):
            assert after.elevation_m == pytest.approx(before.elevation_m)
            assert after.time == before.time

    def test_absence_survives_a_round_trip(self) -> None:
        """The property that matters: missing data must stay missing.

        Writing 0.0 for an absent elevation would make the reimported file claim
        the whole route runs at sea level, and nothing downstream could tell that
        from a genuine measurement.
        """
        original = parse_gpx(GPX_WITHOUT_ELEVATION)

        written = write_gpx(original)
        reparsed = parse_gpx(written.encode())

        assert b"<ele>" not in written.encode()
        assert all(point.elevation_m is None for point in reparsed.points)
        assert "elevation" in reparsed.missing

    def test_name_survives_a_round_trip(self) -> None:
        original = parse_gpx(MINIMAL_GPX)

        reparsed = parse_gpx(write_gpx(original).encode())

        assert reparsed.name == "Clifden to Letterfrack"

    def test_waypoints_survive_a_round_trip(self) -> None:
        track = ParsedTrack(
            name="With stops",
            points=[TrackPoint(lat=53.1, lon=-9.1), TrackPoint(lat=53.2, lon=-9.2)],
            waypoints=[TrackPoint(lat=53.15, lon=-9.15)],
            waypoint_names=["Coffee"],
        )

        reparsed = parse_gpx(write_gpx(track).encode())

        assert len(reparsed.waypoints) == 1
        assert reparsed.waypoint_names == ["Coffee"]

    def test_a_generated_route_is_written_as_a_route_not_a_track(self) -> None:
        """A route Contour planned was never ridden; the file must say so."""
        track = ParsedTrack(
            name="Generated",
            points=[TrackPoint(lat=53.1, lon=-9.1), TrackPoint(lat=53.2, lon=-9.2)],
        )

        written = write_gpx(track, as_route=True)

        assert "<rte>" in written
        assert "<trk>" not in written
        assert len(parse_gpx(written.encode()).points) == 2

    def test_written_files_pass_contour_s_own_validation(self) -> None:
        """Contour must not produce files it would itself refuse."""
        written = write_gpx(parse_gpx(MINIMAL_GPX)).encode()

        detected = validate_upload(written, "export.gpx")

        assert detected.format is FileFormat.GPX

    def test_timestamps_keep_their_timezone(self) -> None:
        track = ParsedTrack(
            name="Timed",
            points=[
                TrackPoint(lat=53.1, lon=-9.1, time=datetime(2026, 7, 26, 9, 0, tzinfo=UTC)),
                TrackPoint(lat=53.2, lon=-9.2, time=datetime(2026, 7, 26, 9, 30, tzinfo=UTC)),
            ],
        )

        reparsed = parse_gpx(write_gpx(track).encode())

        assert reparsed.points[0].time == datetime(2026, 7, 26, 9, 0, tzinfo=UTC)
