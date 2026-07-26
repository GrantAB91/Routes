"""Route intent parsing (§8.2-8.3).

The endpoint parses and returns; it does not route. That separation is the whole
point of §8.3: the user sees exactly what Contour understood, and can correct
any field, before geometry is produced. An endpoint that parsed and routed in
one step would make a mis-parse invisible until a wrong route appeared.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..config import IntentParserName, get_settings
from ..intent.parser import DeterministicIntentParser, intents_requiring_user_input

router = APIRouter(prefix="/v1/intent", tags=["intent"])


class ParseRequest(BaseModel):
    text: str = Field(
        min_length=1,
        max_length=2000,
        description="The rider's request in their own words.",
    )


class ParsedFieldOut(BaseModel):
    value: Any
    phrase: str = Field(description="The words in the request that produced this value.")
    note: str = Field(
        default="",
        description=(
            "Any qualification that must be shown with the value, such as a "
            "preference Contour applies as a labelled proxy rather than as a "
            "measurement."
        ),
    )


class ParseResponse(BaseModel):
    parser: str
    parser_version: str
    fields: dict[str, ParsedFieldOut]
    place_mentions: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "Place names recognised but deliberately not resolved to coordinates. "
            "A geocoder resolves these; Contour never guesses a location."
        ),
    )
    unresolved: list[str] = Field(default_factory=list)
    blocking: list[str] = Field(
        default_factory=list,
        description=(
            "Fields whose absence makes the request impossible to execute. Only "
            "these are worth asking the user about."
        ),
    )
    confirmed: bool = Field(
        default=False,
        description=(
            "Always false from this endpoint. The intent must be confirmed by the "
            "user before it is routed."
        ),
    )


def _parser():
    settings = get_settings()
    if settings.intent_parser is IntentParserName.ANTHROPIC:
        # The model-backed parser is selected by configuration; it produces the
        # same ParsedIntent and is held to the same rules (§8.6).
        from ..intent.anthropic_parser import AnthropicIntentParser

        return AnthropicIntentParser()
    return DeterministicIntentParser()


@router.post(
    "/parse",
    response_model=ParseResponse,
    summary="Read a request into a structured intent",
    description=(
        "Converts free text into constraints and preferences, recording which "
        "phrase produced each value. Resolves no places and produces no geometry. "
        "The result is shown to the user for correction before routing."
    ),
)
async def parse(request: ParseRequest) -> ParseResponse:
    parser = _parser()
    parsed = parser.parse(request.text)

    return ParseResponse(
        parser=parser.name,
        parser_version=parser.version,
        fields={
            key: ParsedFieldOut(
                value=(value.value.value if hasattr(value.value, "value") else value.value),
                phrase=value.phrase,
                note=value.note,
            )
            for key, value in parsed.fields.items()
        },
        place_mentions=parsed.place_mentions,
        unresolved=parsed.unresolved,
        blocking=list(intents_requiring_user_input(parsed)),
    )
