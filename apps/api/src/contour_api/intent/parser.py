"""Natural language to :class:`RouteIntent`.

The parser's job is narrow on purpose. It converts a sentence into structured
constraints and preferences, records which phrase produced each value, and stops.
It resolves no places, invents no geometry, and asserts nothing about surface,
access, elevation, traffic or safety (§8.6). Everything it produces is shown to
the user for correction before any routing happens (§8.3).

The default implementation is deterministic and needs no network and no API key.
That is not a fallback for a missing model — it is the default, because a
route request is a small, regular language and a rule-based reading of it is
reproducible, auditable, and explains itself. A language-model parser is
available behind the same interface for phrasing the rules do not cover.

Two readings of a request deserve special care, and both appear in §8.7's own
examples:

* "reduce traffic exposure" — Contour holds no traffic data at all. The request
  is recorded as a *road class proxy* preference and labelled as such wherever
  it is shown, never as a traffic measurement (§7.7).
* "routes that have verified surface data" — this is not a surface preference,
  it is a tolerance for unknown data. It becomes a limit on unsurveyed distance
  rather than a bias toward gravel.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import ClassVar, Protocol

from ..models.enums import BicycleType

PARSER_VERSION = "deterministic-1"

# 1 mile = 1609.344 m exactly, by international agreement.
METRES_PER_MILE = 1609.344
METRES_PER_FOOT = 0.3048


@dataclass
class ParsedField:
    """One extracted value and the words that produced it."""

    value: object
    phrase: str
    # How the value was arrived at, for the "here is what I understood" screen.
    note: str = ""


@dataclass
class ParsedIntent:
    """The parser's output: fields, provenance and what it could not resolve."""

    fields: dict[str, ParsedField] = field(default_factory=dict)
    # Place names the parser recognised but cannot turn into coordinates. A
    # geocoder resolves these; without one they stay unresolved and the user is
    # asked, rather than a guess being routed.
    place_mentions: dict[str, str] = field(default_factory=dict)
    unresolved: list[str] = field(default_factory=list)
    # Phrases understood but deliberately not acted on, with the reason. Shown
    # so a user is never left thinking Contour honoured something it cannot.
    declined: list[dict[str, str]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def value(self, key: str, default: object = None) -> object:
        parsed = self.fields.get(key)
        return parsed.value if parsed else default

    def trace(self) -> dict[str, dict[str, str]]:
        return {
            key: {"phrase": parsed.phrase, "note": parsed.note}
            for key, parsed in self.fields.items()
        }


class IntentParser(Protocol):
    name: str
    version: str

    def parse(self, text: str) -> ParsedIntent: ...


def _to_metres(value: float, unit: str) -> float:
    unit = unit.lower().rstrip(".")
    if unit in {"km", "kilometre", "kilometres", "kilometer", "kilometers", "k"}:
        return value * 1000.0
    if unit in {"mi", "mile", "miles"}:
        return value * METRES_PER_MILE
    if unit in {"ft", "foot", "feet"}:
        return value * METRES_PER_FOOT
    return value


# Distance and elevation share a number-plus-unit shape, so the unit decides
# which one a phrase is talking about.
_DISTANCE_UNITS = r"km|kilometres|kilometers|kilometre|kilometer|mi|miles|mile|m|metres|meters"
_ELEVATION_UNITS = r"m|metres|meters|ft|feet|foot"

_NUMBER = r"(\d+(?:[.,]\d+)?)"


def _number(raw: str) -> float:
    return float(raw.replace(",", ""))


@dataclass
class DeterministicIntentParser:
    """Rule-based parser (§8.5).

    Patterns are ordered most specific first, because "each day below 100 km"
    and "below 100 km" mean different things and the daily reading must win.
    """

    name: str = "deterministic"
    version: str = PARSER_VERSION

    def parse(self, text: str) -> ParsedIntent:
        parsed = ParsedIntent()
        lowered = text.lower()

        self._parse_bicycle_type(lowered, parsed)
        self._parse_daily_limits(lowered, parsed)
        self._parse_total_distance(lowered, parsed)
        self._parse_gradient(lowered, parsed)
        self._parse_detour(lowered, parsed)
        self._parse_days(lowered, parsed)
        self._parse_preferences(lowered, parsed)
        self._parse_named_route(text, parsed)
        self._parse_places(text, parsed)
        self._check_executability(parsed)

        return parsed

    # -- bicycle ------------------------------------------------------------

    _BICYCLE_PATTERNS: tuple[tuple[str, BicycleType], ...] = (
        (r"\b(road bike|road bicycle|racing bike)\b", BicycleType.ROAD),
        (r"\b(gravel bike|gravel bicycle|adventure bike)\b", BicycleType.CROSS),
        (r"\b(cyclo-?cross|cx bike)\b", BicycleType.CROSS),
        (r"\b(mountain bike|mtb)\b", BicycleType.MOUNTAIN),
        (r"\b(e-?bike|electric bike|pedelec)\b", BicycleType.EBIKE),
        (r"\b(hybrid|touring bike|city bike|commuter)\b", BicycleType.HYBRID),
    )

    def _parse_bicycle_type(self, text: str, parsed: ParsedIntent) -> None:
        for pattern, bicycle in self._BICYCLE_PATTERNS:
            match = re.search(pattern, text)
            if not match:
                continue
            note = ""
            if bicycle is BicycleType.EBIKE:
                # Stated plainly here so it can be shown on the intent screen.
                note = (
                    "E-bike planning adjusts gradient preference only. Contour has "
                    "no battery or range data and makes no claim about either."
                )
            elif bicycle is BicycleType.CROSS and "gravel" in match.group(0):
                note = "Gravel bikes map to the engine's cyclo-cross profile."
            parsed.fields["bicycle_type"] = ParsedField(
                value=bicycle, phrase=match.group(0), note=note
            )
            return

    # -- distance and ascent limits -----------------------------------------

    def _parse_daily_limits(self, text: str, parsed: ParsedIntent) -> None:
        daily = r"(?:each day|per day|a day|daily|every day)"

        # "keep each day below 100 km" / "under 100 km per day"
        for pattern in (
            rf"{daily}[^.]{{0,30}}?(?:below|under|less than|at most|max(?:imum)?(?: of)?)\s*{_NUMBER}\s*({_DISTANCE_UNITS})\b",
            rf"(?:below|under|less than|at most|max(?:imum)?(?: of)?)\s*{_NUMBER}\s*({_DISTANCE_UNITS})\b[^.]{{0,30}}?{daily}",
        ):
            match = re.search(pattern, text)
            if match:
                parsed.fields["max_daily_distance_m"] = ParsedField(
                    value=_to_metres(_number(match.group(1)), match.group(2)),
                    phrase=match.group(0).strip(),
                )
                break

        # Ascent: "1400 metres of ascent", "1400 m climbing"
        ascent_patterns = (
            rf"{_NUMBER}\s*({_ELEVATION_UNITS})\s*(?:of\s*)?(?:ascent|climbing|climb|elevation gain|up)\b",
            rf"(?:ascent|climbing|elevation gain)\s*(?:of|below|under|at most)?\s*{_NUMBER}\s*({_ELEVATION_UNITS})\b",
        )
        for pattern in ascent_patterns:
            match = re.search(pattern, text)
            if not match:
                continue
            metres = _to_metres(_number(match.group(1)), match.group(2))
            # A limit stated alongside a daily distance is itself daily, which
            # is how §8.7's "below 100 kilometres and 1400 metres of ascent"
            # must read. Attaching it to the whole route would silently make a
            # two-week tour a 1,400 m tour.
            key = (
                "max_daily_ascent_m"
                if "max_daily_distance_m" in parsed.fields or re.search(daily, text)
                else "max_total_ascent_m"
            )
            parsed.fields[key] = ParsedField(
                value=metres,
                phrase=match.group(0).strip(),
                note=(
                    "Read as a daily limit because the request also sets a daily distance."
                    if key == "max_daily_ascent_m" and "max_daily_distance_m" in parsed.fields
                    else ""
                ),
            )
            break

    def _parse_total_distance(self, text: str, parsed: ParsedIntent) -> None:
        if "max_daily_distance_m" in parsed.fields:
            return
        match = re.search(
            rf"(?:total|overall|no more than|under|below|at most)\s*{_NUMBER}\s*({_DISTANCE_UNITS})\b",
            text,
        )
        if match:
            parsed.fields["max_total_distance_m"] = ParsedField(
                value=_to_metres(_number(match.group(1)), match.group(2)),
                phrase=match.group(0).strip(),
            )

    def _parse_gradient(self, text: str, parsed: ParsedIntent) -> None:
        match = re.search(
            rf"(?:above|over|steeper than|more than|exceed(?:ing|s)?|max(?:imum)?(?: gradient| grade)?(?: of)?|gradient (?:above|over|of)|below|under)\s*{_NUMBER}\s*(?:%|per ?cent|percent)",
            text,
        )
        if not match:
            return

        note = ""
        # "avoid anything above 12%" states a ceiling; so does "keep it under
        # 12%". Both become the same constraint.
        if re.search(r"\bwhere a compliant alternative exists\b|\bif possible\b", text):
            note = (
                "Recorded as a hard limit. Where no compliant route exists, Contour "
                "returns the closest route and names the segments that exceed it "
                "rather than dropping the limit."
            )
        parsed.fields["max_gradient_percent"] = ParsedField(
            value=_number(match.group(1)), phrase=match.group(0).strip(), note=note
        )

    def _parse_detour(self, text: str, parsed: ParsedIntent) -> None:
        match = re.search(
            rf"detour[^.]{{0,20}}?(?:below|under|less than|at most|of)\s*{_NUMBER}\s*(?:%|per ?cent|percent)",
            text,
        )
        if not match:
            match = re.search(
                rf"(?:below|under|less than|at most)\s*{_NUMBER}\s*(?:%|per ?cent|percent)[^.]{{0,20}}?detour",
                text,
            )
        if match:
            parsed.fields["max_detour_ratio"] = ParsedField(
                value=1.0 + _number(match.group(1)) / 100.0,
                phrase=match.group(0).strip(),
                note="Expressed as a ratio against the shortest compliant route.",
            )

    def _parse_days(self, text: str, parsed: ParsedIntent) -> None:
        match = re.search(rf"(?:in|over|across)\s*{_NUMBER}\s*days?\b", text)
        if match:
            parsed.fields["number_of_days"] = ParsedField(
                value=int(float(match.group(1))), phrase=match.group(0).strip()
            )

    # -- preferences --------------------------------------------------------

    def _parse_preferences(self, text: str, parsed: ParsedIntent) -> None:
        if re.search(
            r"\b(minimi[sz]e|reduce|less|lower|least)\s+(ascent|climbing|elevation)", text
        ):
            parsed.fields["avoid_hills"] = ParsedField(value=1.0, phrase="minimise ascent")
        elif re.search(r"\blower ascent\b", text):
            parsed.fields["avoid_hills"] = ParsedField(value=1.0, phrase="lower ascent")

        if re.search(r"\b(follow|hug|stay near|close to|along)\s+the\s+coast|coastal\b", text):
            parsed.fields["coast_preference"] = ParsedField(value=True, phrase="follow the coast")

        if re.search(
            r"\bstay close to the official (route|line)\b|\bclose to the official\b", text
        ):
            parsed.fields["corridor_preference"] = ParsedField(
                value=True, phrase="stay close to the official route"
            )

        # Contour holds no traffic data. This is recorded as a proxy and
        # labelled, never as a traffic measurement (§7.7).
        if re.search(r"\b(traffic|busy roads?|quiet(er)? roads?)\b", text):
            parsed.fields["reduce_road_exposure"] = ParsedField(
                value=True,
                phrase="reduce traffic exposure",
                note=(
                    "Contour has no traffic data. This is applied as a road-class "
                    "proxy — preferring smaller roads — and is reported as a proxy "
                    "everywhere it appears, not as measured traffic."
                ),
            )

        if re.search(
            r"\b(cycle|cycling|bike)\s+(infrastructure|lanes?|paths?|tracks?|network)", text
        ):
            parsed.fields["prefer_cycle_infrastructure"] = ParsedField(
                value=1.0, phrase="prefer cycle infrastructure"
            )

        if re.search(
            r"\b(legal|permitted|allowed)\s+(cycling |bicycle |bike )?(roads?|routes?)\b", text
        ):
            parsed.fields["require_legal_access"] = ParsedField(
                value=True,
                phrase="use legal cycling roads",
                note=(
                    "Legal bicycle access is always required. Segments with no "
                    "recorded access are reported as unverified rather than assumed "
                    "legal."
                ),
            )

        if re.search(r"\b(unsealed|unpaved|gravel|dirt|off-?road)\b", text):
            parsed.fields["prefer_gravel"] = ParsedField(value=1.0, phrase="favour unsealed routes")
        if re.search(r"\b(paved|sealed|tarmac|asphalt)\b", text) and not re.search(
            r"\bunpaved|unsealed\b", text
        ):
            parsed.fields["prefer_paved"] = ParsedField(value=1.0, phrase="prefer paved")

        # "verified surface data" is a statement about data quality, not about
        # the surface itself.
        if re.search(r"\bverified\s+(surface|data)|\bwith verified\b|\bknown surface\b", text):
            parsed.fields["require_known_surface"] = ParsedField(
                value=True,
                phrase="verified surface data",
                note=(
                    "Applied as a limit on how much route may have no surface data, "
                    "not as a preference between surfaces."
                ),
            )

        if re.search(r"\bavoid (the )?ferr(y|ies)\b|\bno ferr(y|ies)\b", text):
            parsed.fields["ferry_preference"] = ParsedField(value="avoid", phrase="avoid ferries")
        elif re.search(r"\b(use|take|include) (the )?ferr(y|ies)\b", text):
            parsed.fields["ferry_preference"] = ParsedField(value="prefer", phrase="use ferries")

    # -- named routes and places --------------------------------------------

    _NAMED_ROUTES: ClassVar[dict[str, str]] = {
        "wild atlantic way": "wild-atlantic-way",
        "eurovelo 1": "eurovelo-1",
        "atlantic coast route": "eurovelo-1",
    }

    def _parse_named_route(self, text: str, parsed: ParsedIntent) -> None:
        lowered = text.lower()
        for name, slug in self._NAMED_ROUTES.items():
            if name in lowered:
                parsed.fields["named_route"] = ParsedField(
                    value=slug,
                    phrase=name,
                    note=(
                        "Matched against Contour's route catalogue. The official "
                        "line is a touring corridor, not automatically a legal or "
                        "suitable cycling route."
                    ),
                )
                return

    # Both apostrophe forms are intentional: Irish place names carry them in
    # either encoding depending on the source, and dropping the typographic one
    # would silently fail to match names like O'Brien's Bridge (typographic form).
    _FROM_TO: ClassVar[re.Pattern[str]] = re.compile(
        r"\bfrom\s+([A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"  # noqa: RUF001
        r"\s+to\s+([A-Z][\w'’\-]*(?:\s+[A-Z][\w'’\-]*)*)"  # noqa: RUF001
    )

    def _parse_places(self, text: str, parsed: ParsedIntent) -> None:
        """Record place mentions without resolving them.

        Turning a name into coordinates is a geocoder's job. The parser only
        notes what was named, so that an unavailable geocoder produces an
        explicit unresolved field rather than a plausible wrong location.
        """
        match = self._FROM_TO.search(text)
        if match:
            parsed.place_mentions["origin"] = match.group(1)
            parsed.place_mentions["destination"] = match.group(2)

    # -- executability ------------------------------------------------------

    def _check_executability(self, parsed: ParsedIntent) -> None:
        """Record only what genuinely blocks execution (§8.5.2).

        The parser must not interrogate the user about every unset field. A
        request is executable when Contour knows where the route goes, whether
        by named route or by endpoints.
        """
        has_named_route = "named_route" in parsed.fields
        has_endpoints = {"origin", "destination"} <= parsed.place_mentions.keys()

        if not has_named_route and not has_endpoints:
            parsed.unresolved.append("origin_and_destination")
        elif has_endpoints:
            parsed.unresolved.append("origin_and_destination_need_geocoding")


def intents_requiring_user_input(parsed: ParsedIntent) -> Iterable[str]:
    """Fields that make execution impossible while unset."""
    return (field for field in parsed.unresolved if field == "origin_and_destination")
