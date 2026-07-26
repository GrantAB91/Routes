"""API surface tests.

Covers the conventions the whole API depends on: structured errors with a
request id, the difference between a disabled capability and a broken one, and
that intent parsing returns what Contour understood without routing anything.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from contour_api.main import create_app


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


class TestHealth:
    def test_liveness_needs_no_dependencies(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.status_code == 200
        assert response.json() == {"status": "ok"}

    def test_every_response_carries_a_request_id(self, client: TestClient) -> None:
        response = client.get("/health")

        assert response.headers["x-request-id"]

    def test_a_supplied_request_id_is_preserved(self, client: TestClient) -> None:
        """Tracing across the web app and API needs the id to survive the hop."""
        response = client.get("/health", headers={"x-request-id": "abc123"})

        assert response.headers["x-request-id"] == "abc123"

    def test_component_report_distinguishes_disabled_from_unavailable(
        self, client: TestClient
    ) -> None:
        """A switched-off capability is a deployment choice, not an incident."""
        payload = client.get("/health/components").json()

        assert set(payload["components"]) >= {
            "database",
            "routing",
            "elevation",
            "object_storage",
            "disk",
        }
        for name, check in payload["components"].items():
            assert check["status"] in {
                "healthy",
                "degraded",
                "unavailable",
                "disabled",
            }, name
            # Anything not working must say what would make it work.
            if check["status"] in {"disabled", "unavailable"}:
                assert check.get("remedy") or check.get("detail"), name

    def test_a_disabled_capability_does_not_make_the_system_unhealthy(
        self, client: TestClient
    ) -> None:
        payload = client.get("/health/components").json()

        disabled = [
            name for name, check in payload["components"].items() if check["status"] == "disabled"
        ]
        assert disabled, "expected at least elevation to be disabled by default"
        assert not set(disabled) & set(payload["unavailable"])

    def test_a_running_engine_without_tiles_is_not_reported_healthy(
        self, client: TestClient
    ) -> None:
        """The most misleading green light available, so it is tested for.

        Valhalla answers /status perfectly with no tiles built while being unable
        to route a single metre.
        """
        routing = client.get("/health/components").json()["components"]["routing"]

        if routing["status"] == "disabled":
            pytest.skip("no Valhalla endpoint configured")
        if routing.get("tile_count", 0) == 0:
            assert routing["status"] == "degraded"
            assert "no routing tiles" in routing["detail"]
            assert "build-tiles" in routing["remedy"]


class TestIntentEndpoint:
    def test_a_request_is_parsed_into_fields_with_their_phrases(self, client: TestClient) -> None:
        response = client.post(
            "/v1/intent/parse",
            json={
                "text": (
                    "Plan the Wild Atlantic Way for a road bike, keep each day "
                    "below 100 km and 1400 m of ascent."
                )
            },
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["parser"] == "deterministic"
        assert payload["fields"]["bicycle_type"]["value"] == "road"
        assert payload["fields"]["max_daily_distance_m"]["value"] == 100_000.0
        assert payload["fields"]["max_daily_ascent_m"]["value"] == 1400.0
        # Every value must show the words it came from, so a wrong reading is
        # correctable rather than mysterious.
        assert payload["fields"]["bicycle_type"]["phrase"] == "road bike"

    def test_parsing_never_confirms_the_intent_itself(self, client: TestClient) -> None:
        """§8.3: the user confirms, not the parser."""
        payload = client.post(
            "/v1/intent/parse", json={"text": "Plan the Wild Atlantic Way."}
        ).json()

        assert payload["confirmed"] is False

    def test_a_proxy_preference_is_labelled_in_the_response(self, client: TestClient) -> None:
        """Contour has no traffic data and the API must not imply it does."""
        payload = client.post(
            "/v1/intent/parse", json={"text": "Follow the coast and avoid traffic."}
        ).json()

        note = payload["fields"]["reduce_road_exposure"]["note"]
        assert "no traffic data" in note
        assert "proxy" in note

    def test_place_names_are_returned_unresolved(self, client: TestClient) -> None:
        payload = client.post(
            "/v1/intent/parse", json={"text": "Plan a route from Galway to Clifden."}
        ).json()

        assert payload["place_mentions"] == {
            "origin": "Galway",
            "destination": "Clifden",
        }

    def test_only_genuinely_blocking_gaps_are_flagged(self, client: TestClient) -> None:
        payload = client.post("/v1/intent/parse", json={"text": "Something hilly please."}).json()

        assert payload["blocking"] == ["origin_and_destination"]

    def test_an_empty_request_is_rejected_by_schema(self, client: TestClient) -> None:
        response = client.post("/v1/intent/parse", json={"text": ""})

        assert response.status_code == 422


class TestOpenAPI:
    def test_the_schema_is_generated(self, client: TestClient) -> None:
        """The typed frontend client is generated from this (§19.2)."""
        schema = client.get("/openapi.json").json()

        assert schema["info"]["title"] == "Contour API"
        assert "/v1/intent/parse" in schema["paths"]
        assert "/health/components" in schema["paths"]

    def test_the_schema_documents_the_honesty_conventions(self, client: TestClient) -> None:
        """A client author must learn the four-state verdict from the contract."""
        description = client.get("/openapi.json").json()["info"]["description"]

        assert "Unknown is a value" in description
        assert "Disabled is not broken" in description


class TestImportEndpoint:
    """Import works today, so it is tested end to end through the API."""

    MINIMAL_GPX = (
        b'<?xml version="1.0" encoding="UTF-8"?>'
        b'<gpx version="1.1" creator="test" xmlns="http://www.topografix.com/GPX/1/1">'
        b"<trk><name>Connemara loop</name><trkseg>"
        b'<trkpt lat="53.4890" lon="-10.0200"><ele>12.4</ele></trkpt>'
        b'<trkpt lat="53.5497" lon="-9.9469"><ele>21.0</ele></trkpt>'
        b"</trkseg></trk></gpx>"
    )

    def test_a_valid_gpx_is_previewed_before_anything_is_derived(self, client: TestClient) -> None:
        response = client.post(
            "/v1/imports",
            files={"file": ("route.gpx", self.MINIMAL_GPX, "application/gpx+xml")},
        )

        assert response.status_code == 200
        payload = response.json()
        assert payload["name"] == "Connemara loop"
        assert payload["point_count"] == 2
        assert payload["has_elevation"] is True
        assert payload["checksum_sha256"]
        # The user is told the original survives before they commit to anything.
        assert any("preserved unchanged" in note for note in payload["notes"])

    def test_absent_elevation_is_reported_rather_than_zero_filled(self, client: TestClient) -> None:
        gpx = (
            b'<?xml version="1.0"?>'
            b'<gpx version="1.1" xmlns="http://www.topografix.com/GPX/1/1">'
            b'<trk><trkseg><trkpt lat="53.1" lon="-9.1"/><trkpt lat="53.2" lon="-9.2"/>'
            b"</trkseg></trk></gpx>"
        )

        payload = client.post(
            "/v1/imports", files={"file": ("route.gpx", gpx, "application/gpx+xml")}
        ).json()

        assert payload["has_elevation"] is False
        assert "elevation" in payload["missing"]
        assert payload["first_points"][0]["elevation_m"] is None

    def test_a_hostile_file_is_refused_with_a_stable_code(self, client: TestClient) -> None:
        hostile = b'<!DOCTYPE gpx [<!ENTITY x "y">]><gpx version="1.1"><trk/></gpx>'

        response = client.post(
            "/v1/imports", files={"file": ("route.gpx", hostile, "application/gpx+xml")}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "xml_doctype_forbidden"

    def test_an_unimplemented_format_says_so_rather_than_failing_vaguely(
        self, client: TestClient
    ) -> None:
        """§24.3: incomplete support is stated, not implied by a generic error."""
        response = client.post(
            "/v1/imports",
            files={"file": ("route.json", b'{"type":"FeatureCollection"}', "application/json")},
        )

        assert response.status_code == 422
        assert "not implemented" in response.json()["error"]["message"]

    def test_limits_declare_which_formats_actually_work(self, client: TestClient) -> None:
        payload = client.get("/v1/imports/limits").json()

        assert payload["formats"]["gpx"]["read"] is True
        assert payload["formats"]["fit"]["read"] is False
        assert payload["formats"]["fit"]["reason"]
