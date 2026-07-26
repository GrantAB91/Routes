"""Coverage and source transparency endpoints (§4.2).

This is the screen that stops Contour implying it knows more than it does. It
reports every connected source, every source that contributes nothing and why,
and every licence whose terms nobody has verified.

``/v1/coverage/claim`` exists so the interface never has to compose the sentence
itself: it returns the qualified statement Contour is entitled to make about its
own coverage, which §4.3 requires instead of the phrase "all routes".
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ..sources.licensing import Action, SourceTerms, evaluate
from ..sources.registry import REGISTRY, CoverageReport

router = APIRouter(prefix="/v1/coverage", tags=["coverage"])


@router.get("", summary="Connected sources, gaps and licences")
async def coverage() -> dict[str, Any]:
    return CoverageReport().as_dict()


@router.get(
    "/claim",
    summary="The coverage statement Contour is entitled to make",
    description=(
        "Returns a qualified description of what the catalogue actually holds. "
        "§4.3 forbids the phrase 'all routes' without this qualification, so the "
        "interface renders this sentence rather than writing its own."
    ),
)
async def claim() -> dict[str, Any]:
    report = CoverageReport()
    importable = [e for e in REGISTRY if e.is_importable]
    gaps = report.gaps()

    if not importable:
        statement = (
            "Contour currently holds no imported route sources. Nothing in the "
            "catalogue comes from an external publisher."
        )
    else:
        statement = (
            "Contour's catalogue covers "
            + ", ".join(e.name for e in importable)
            + ". "
            + f"{len(gaps)} further source(s) are configured but contribute nothing; "
            "see the gaps list for the reason in each case."
        )

    return {
        "statement": statement,
        "importable_sources": [e.slug for e in importable],
        "gap_count": len(gaps),
        "gaps": gaps,
        "unverified_licences": report.unverified_licences(),
        # Stated explicitly so no client can render a completeness claim.
        "is_complete_coverage": False,
        "completeness_note": (
            "Contour does not claim complete coverage of cycling routes in any "
            "region. Route absence from the catalogue means no connected source "
            "published it, not that no route exists."
        ),
    }


@router.get(
    "/licences",
    summary="Licence terms per source",
    description=(
        "What each source permits, and what Contour will refuse. Sources whose "
        "terms have not been verified are refused for export and publish."
    ),
)
async def licences() -> dict[str, Any]:
    rows = []
    for source in REGISTRY:
        terms = SourceTerms(
            source_slug=source.slug,
            source_name=source.name,
            licence_identifier=source.licence_identifier,
            licence_name=source.licence_name,
            redistribution=source.redistribution,
            attribution_text=source.attribution_text,
            restriction_summary=source.redistribution_restrictions,
        )
        rows.append(
            {
                "source": source.slug,
                "name": source.name,
                "licence": source.licence_name,
                "licence_url": source.licence_url,
                "verified": source.licence_verified,
                "attribution": source.attribution_text,
                "export": evaluate(Action.EXPORT, [terms]).as_dict(),
                "publish": evaluate(Action.PUBLISH, [terms]).as_dict(),
            }
        )

    return {"licences": rows}
