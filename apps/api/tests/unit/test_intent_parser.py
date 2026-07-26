"""Intent parser tests.

The six requests in §8.7 are the acceptance target and each has its own test.
Beyond parsing correctly, two behaviours are defended: the parser must never
turn a phrase into a claim Contour cannot support, and it must never resolve a
place name into coordinates by guessing.
"""

from __future__ import annotations

import pytest

from contour_api.intent.parser import DeterministicIntentParser, intents_requiring_user_input
from contour_api.models.enums import BicycleType


@pytest.fixture
def parser() -> DeterministicIntentParser:
    return DeterministicIntentParser()


class TestSpecifiedRequests:
    """§8.7's own examples, verbatim."""

    def test_complete_wild_atlantic_way_road_bike(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse(
            "Plan the complete Wild Atlantic Way for a road bike, stay close to the "
            "official route, use legal cycling roads, and minimise ascent."
        )

        assert parsed.value("named_route") == "wild-atlantic-way"
        assert parsed.value("bicycle_type") is BicycleType.ROAD
        assert parsed.value("corridor_preference") is True
        assert parsed.value("require_legal_access") is True
        assert parsed.value("avoid_hills") == 1.0
        # A named route is enough to execute; no question needs asking.
        assert list(intents_requiring_user_input(parsed)) == []

    def test_daily_distance_and_ascent(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse("Keep each day below 100 kilometres and 1400 metres of ascent.")

        assert parsed.value("max_daily_distance_m") == pytest.approx(100_000.0)
        # The ascent figure must attach to the day, not the whole tour.
        assert parsed.value("max_daily_ascent_m") == pytest.approx(1400.0)
        assert parsed.value("max_total_ascent_m") is None

    def test_lower_ascent_alternative(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse(
            "Create a lower ascent alternative and show exactly how much coast and "
            "cycle infrastructure I lose."
        )

        assert parsed.value("avoid_hills") == 1.0
        assert parsed.value("prefer_cycle_infrastructure") == 1.0

    def test_gradient_ceiling_with_fallback(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse(
            "Avoid any segment above 12 percent where a compliant alternative exists."
        )

        assert parsed.value("max_gradient_percent") == pytest.approx(12.0)
        # "where a compliant alternative exists" must not soften the limit into
        # a preference; it describes what happens when none exists.
        note = parsed.fields["max_gradient_percent"].note
        assert "hard limit" in note
        assert "closest route" in note

    def test_coast_traffic_and_detour(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse(
            "Follow the coast, reduce traffic exposure, and keep the detour below 15 percent."
        )

        assert parsed.value("coast_preference") is True
        assert parsed.value("reduce_road_exposure") is True
        assert parsed.value("max_detour_ratio") == pytest.approx(1.15)

    def test_gravel_with_verified_surface(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse(
            "Make this route suitable for a gravel bike and favour unsealed routes "
            "that have verified surface data."
        )

        assert parsed.value("bicycle_type") is BicycleType.CROSS
        assert parsed.value("prefer_gravel") == 1.0
        # "verified surface data" is a data-quality requirement, not a surface
        # preference, and must not silently become one.
        assert parsed.value("require_known_surface") is True


class TestHonesty:
    def test_traffic_request_is_recorded_as_a_proxy(
        self, parser: DeterministicIntentParser
    ) -> None:
        """Contour has no traffic data and must not imply otherwise."""
        parsed = parser.parse("Take quieter roads and reduce traffic exposure.")

        note = parsed.fields["reduce_road_exposure"].note
        assert "no traffic data" in note
        assert "proxy" in note

    def test_ebike_makes_no_range_claim(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse("Plan this for an e-bike.")

        assert parsed.value("bicycle_type") is BicycleType.EBIKE
        assert "no battery or range data" in parsed.fields["bicycle_type"].note

    def test_named_route_is_not_asserted_to_be_rideable(
        self, parser: DeterministicIntentParser
    ) -> None:
        parsed = parser.parse("Plan the Wild Atlantic Way.")

        note = parsed.fields["named_route"].note
        assert "touring corridor" in note

    def test_legal_access_note_does_not_promise_verification(
        self, parser: DeterministicIntentParser
    ) -> None:
        parsed = parser.parse("Use legal cycling roads only.")

        assert "assumed" in parsed.fields["require_legal_access"].note

    def test_place_names_are_recorded_not_resolved(self, parser: DeterministicIntentParser) -> None:
        """A guessed coordinate is worse than an unresolved field."""
        parsed = parser.parse("Plan a route from Galway to Clifden for a road bike.")

        assert parsed.place_mentions["origin"] == "Galway"
        assert parsed.place_mentions["destination"] == "Clifden"
        assert "origin_and_destination_need_geocoding" in parsed.unresolved

    def test_a_request_with_no_destination_is_blocked(
        self, parser: DeterministicIntentParser
    ) -> None:
        """The one case worth interrupting the user for (§8.5.2)."""
        parsed = parser.parse("Plan me something hilly for a road bike.")

        assert list(intents_requiring_user_input(parsed)) == ["origin_and_destination"]


class TestUnits:
    def test_miles_are_converted(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse("Keep each day under 60 miles.")

        assert parsed.value("max_daily_distance_m") == pytest.approx(96_560.64)

    def test_feet_of_ascent_are_converted(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse("Keep each day below 50 miles and 4000 feet of ascent.")

        assert parsed.value("max_daily_ascent_m") == pytest.approx(1219.2)

    def test_thousands_separators_are_handled(self, parser: DeterministicIntentParser) -> None:
        parsed = parser.parse("Keep each day below 1,400 metres of ascent.")

        assert parsed.value("max_daily_ascent_m") == pytest.approx(1400.0)


class TestTrace:
    def test_every_field_records_the_phrase_that_produced_it(
        self, parser: DeterministicIntentParser
    ) -> None:
        """A wrong reading must be correctable, which means it must be visible."""
        parsed = parser.parse("Plan the Wild Atlantic Way for a gravel bike, each day below 90 km.")

        trace = parsed.trace()
        assert trace["bicycle_type"]["phrase"] == "gravel bike"
        assert "90 km" in trace["max_daily_distance_m"]["phrase"]
        assert all(entry["phrase"] for entry in trace.values())

    def test_parser_identifies_itself(self, parser: DeterministicIntentParser) -> None:
        """Stored intents must record which parser produced them."""
        assert parser.name == "deterministic"
        assert parser.version


def test_ferry_preferences(parser: DeterministicIntentParser) -> None:
    assert DeterministicIntentParser().parse("Avoid ferries.").value("ferry_preference") == "avoid"
    assert (
        DeterministicIntentParser().parse("Use the ferry where it helps.").value("ferry_preference")
        == "prefer"
    )


def test_an_empty_request_yields_nothing_and_asks(
    parser: DeterministicIntentParser,
) -> None:
    parsed = parser.parse("")

    assert parsed.fields == {}
    assert list(intents_requiring_user_input(parsed)) == ["origin_and_destination"]
