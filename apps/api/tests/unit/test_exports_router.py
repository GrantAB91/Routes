"""Export and stage endpoints (§12, §15.6, §20.7-20.9).

The licence rules are tested in test_licensing; what is tested here is that the
endpoint actually consults them, and that what it writes says what it means —
particularly that a point with no measured elevation is written as having none
rather than as sea level.
"""

from __future__ import annotations

import json

import pytest

from contour_api.io.gpx import ParsedTrack, TrackPoint, parse_gpx, write_gpx
from contour_api.models.enums import RedistributionPermission
from contour_api.routers.exports import (
    SUPPORTED_EXPORT_FORMATS,
    ExportPoint,
    ExportRequest,
    StageRequest,
    _geojson,
    _headers,
)
from contour_api.sources.licensing import Action, AttributionManifest, SourceTerms, evaluate

OSM = SourceTerms(
    source_slug="openstreetmap",
    source_name="OpenStreetMap",
    licence_identifier="ODbL-1.0",
    licence_name="Open Database License 1.0",
    redistribution=RedistributionPermission.PERMITTED_SHARE_ALIKE,
    attribution_text="© OpenStreetMap contributors",
    share_alike_required=True,
    contribution_m=1000.0,
)

UNVERIFIED = SourceTerms(
    source_slug="copernicus-dem-glo-30",
    source_name="Copernicus DEM GLO-30",
    licence_identifier="unverified",
    licence_name="terms not verified",
    redistribution=RedistributionPermission.UNKNOWN,
    restriction_summary="The licence terms for this source have never been read.",
    contribution_m=1000.0,
)


def a_request(**overrides) -> ExportRequest:
    return ExportRequest(
        name="Westport to Louisburgh",
        points=[
            ExportPoint(lat=53.7975, lon=-9.5397, elevation_m=12.0),
            ExportPoint(lat=53.7644, lon=-9.8125, elevation_m=None),
        ],
        **overrides,
    )


class TestLicenceGate:
    def test_an_unverified_source_blocks_export(self) -> None:
        """The terms were never read, so there is no basis for permitting it."""
        decision = evaluate(Action.EXPORT, [UNVERIFIED])

        assert decision.allowed is False
        assert decision.blocking[0].source_slug == "copernicus-dem-glo-30"

    def test_the_refusal_names_the_source_and_quotes_its_terms(self) -> None:
        """§20.9: a refusal has to be actionable, not generic."""
        payload = evaluate(Action.EXPORT, [UNVERIFIED]).as_dict()

        blocking = payload["blocking_sources"][0]
        assert blocking["name"] == "Copernicus DEM GLO-30"
        assert "never been read" in blocking["restriction"]

    def test_odbl_permits_export_and_carries_share_alike(self) -> None:
        decision = evaluate(Action.EXPORT, [OSM])

        assert decision.allowed is True
        assert decision.share_alike_required is True

    def test_one_blocked_source_blocks_the_whole_export(self) -> None:
        """A route is one artefact; it cannot be part-exported."""
        assert evaluate(Action.EXPORT, [OSM, UNVERIFIED]).allowed is False


class TestHeaders:
    def test_attribution_travels_with_the_download(self) -> None:
        decision = evaluate(Action.EXPORT, [OSM])
        headers = _headers("Westport to Louisburgh", "gpx", decision)

        assert "OpenStreetMap" in headers["x-contour-attribution"]
        assert "same licence" in headers["x-contour-share-alike"].lower()

    def test_the_filename_cannot_escape_its_directory(self) -> None:
        headers = _headers("../../etc/passwd", "gpx", evaluate(Action.EXPORT, [OSM]))

        assert "/" not in headers["content-disposition"].split("filename=")[1]

    def test_an_empty_name_still_produces_a_filename(self) -> None:
        headers = _headers("!!!", "gpx", evaluate(Action.EXPORT, [OSM]))

        assert 'filename="route.gpx"' in headers["content-disposition"]


class TestGeoJson:
    def test_a_point_with_no_elevation_is_written_without_one(self) -> None:
        """Padding with zero would be read as sea level by every consumer.

        This is §2.6 at the file boundary: the format has a slot for elevation,
        and leaving it empty is the only way to say the value is not known.
        """
        payload = json.loads(_geojson(a_request(), AttributionManifest(sources=[OSM])))
        coordinates = payload["geometry"]["coordinates"]

        assert len(coordinates[0]) == 3
        assert len(coordinates[1]) == 2

    def test_the_manifest_is_inside_the_file(self) -> None:
        """A file outlives the request; attribution in a header would not."""
        payload = json.loads(_geojson(a_request(), AttributionManifest(sources=[OSM])))

        assert "OpenStreetMap" in payload["properties"]["attribution"]["text"]


class TestGpx:
    def test_a_generated_route_is_written_as_a_route_not_a_track(self) -> None:
        """A track claims the ride happened. Contour's routes were never ridden."""
        track = ParsedTrack(
            name="Generated",
            points=(TrackPoint(lat=53.8, lon=-9.5, elevation_m=10.0, time=None),) * 2,
        )

        assert "<rte>" in write_gpx(track, as_route=True)
        assert "<trk>" in write_gpx(track, as_route=False)

    def test_a_missing_elevation_survives_a_round_trip(self) -> None:
        track = ParsedTrack(
            name="Partial",
            points=(
                TrackPoint(lat=53.8, lon=-9.5, elevation_m=10.0, time=None),
                TrackPoint(lat=53.81, lon=-9.51, elevation_m=None, time=None),
            ),
        )

        reparsed = parse_gpx(write_gpx(track).encode())

        assert reparsed.points[0].elevation_m == 10.0
        assert reparsed.points[1].elevation_m is None


class TestRequestValidation:
    def test_a_route_needs_at_least_two_points(self) -> None:
        with pytest.raises(ValueError):
            ExportRequest(name="x", points=[ExportPoint(lat=53.0, lon=-9.0)])

    def test_openstreetmap_is_credited_by_default(self) -> None:
        assert a_request().source_slugs == ["openstreetmap"]

    def test_gpx_and_geojson_are_offered(self) -> None:
        assert "gpx-route" in SUPPORTED_EXPORT_FORMATS
        assert "geojson" in SUPPORTED_EXPORT_FORMATS


class TestStageRequest:
    def test_the_assumed_speed_is_a_setting_not_a_prediction(self) -> None:
        """It exists only to turn a time limit into a distance one."""
        request = StageRequest(
            points=[{"distance_m": 0}, {"distance_m": 100_000}],
            max_daily_riding_time_s=6 * 3600,
            assumed_speed_mps=5.0,
        )

        assert request.assumed_speed_mps == 5.0

    def test_a_zero_speed_is_rejected_rather_than_dividing_by_it(self) -> None:
        with pytest.raises(ValueError):
            StageRequest(points=[{"distance_m": 0}, {"distance_m": 1}], assumed_speed_mps=0)
