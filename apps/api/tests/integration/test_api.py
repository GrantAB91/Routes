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
