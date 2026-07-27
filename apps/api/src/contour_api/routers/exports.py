"""Export and stage planning over HTTP (§12, §15.6, §20.7-20.9).

Two endpoints that share one rule: nothing leaves Contour without the licence
engine having said it may. The check runs *before* the file is produced, not
after, so a refused export never exists as bytes anywhere — and the refusal
names the source, quotes its terms and says what would unblock it, rather than
reporting a generic failure (§20.9).

Every export carries an attribution manifest, assembled from the sources that
actually contributed to the route so it credits neither more nor less than the
work involved: crediting a source that supplied nothing is false, and omitting
one that did is a licence breach.

Stage planning shares the shape — take a route, apply the rider's limits, return
what is achievable and say plainly where it is not. A plan that cannot meet the
constraints comes back with the reason rather than silently relaxed (§12.3).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..io.gpx import ParsedTrack, TrackPoint, write_gpx
from ..journeys.stages import RoutePoint, StageConstraints, plan_stages
from ..models.enums import RedistributionPermission, StageBalanceStrategy
from ..models.source import RouteSource, SourceAttribution, SourceLicence
from ..sources.licensing import Action, AttributionManifest, SourceTerms, evaluate

router = APIRouter(prefix="/v1", tags=["export"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Formats Contour can currently write. Declared by name so a client is told
# which formats exist and which do not, rather than discovering it from a
# failure (§15.2).
SUPPORTED_EXPORT_FORMATS = ("gpx-route", "gpx-track", "geojson")


class ExportPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    elevation_m: float | None = Field(
        default=None,
        description=(
            "Null where the elevation source had no coverage. Written as a point "
            "carrying no elevation rather than as zero, which reads as sea level."
        ),
    )


class ExportRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    points: list[ExportPoint] = Field(min_length=2, max_length=200_000)
    description: str | None = Field(default=None, max_length=2000)
    format: str = Field(default="gpx-route")
    source_slugs: list[str] = Field(
        default_factory=lambda: ["openstreetmap"],
        description=(
            "The sources that contributed to this route. Each one's licence is "
            "checked before anything is written, and each is credited in the "
            "manifest."
        ),
    )
    commercial_use: bool = Field(
        default=False,
        description=(
            "Whether the export is for a commercial purpose. Some licences permit "
            "redistribution but not commercial use, so this changes the answer."
        ),
    )


class StagePointIn(BaseModel):
    distance_m: float = Field(ge=0)
    ascent_from_start_m: float | None = None
    name: str | None = None
    is_overnight_candidate: bool = True
    has_accommodation: bool | None = None
    has_water: bool | None = None
    has_food: bool | None = None


class StageRequest(BaseModel):
    points: list[StagePointIn] = Field(min_length=2)
    max_daily_distance_m: float | None = Field(default=None, gt=0)
    min_daily_distance_m: float | None = Field(default=None, ge=0)
    max_daily_ascent_m: float | None = Field(default=None, ge=0)
    max_daily_riding_time_s: int | None = Field(default=None, gt=0)
    assumed_speed_mps: float = Field(
        default=4.2,
        gt=0,
        description=(
            "Used only to convert a time limit into a distance one. Contour does "
            "not predict a rider's speed; this is a stated assumption they can "
            "change, not a model of them."
        ),
    )
    target_days: int | None = Field(default=None, gt=0)
    locked_end_distances_m: list[float] = Field(default_factory=list)
    strategy: StageBalanceStrategy = StageBalanceStrategy.DISTANCE


# --- licence lookup ----------------------------------------------------------


async def load_terms(session: AsyncSession, slugs: list[str]) -> list[SourceTerms]:
    """Read the licence facts for the named sources.

    A source with no licence row is not skipped and not assumed permissive. It
    is returned with ``UNKNOWN`` redistribution, which the engine treats as
    blocking — the terms were never read, so there is no basis for saying the
    export is permitted (§20.4).
    """
    rows = (
        await session.execute(
            select(RouteSource, SourceLicence)
            .outerjoin(SourceLicence, RouteSource.licence_id == SourceLicence.id)
            .where(RouteSource.slug.in_(slugs))
        )
    ).all()

    found = {source.slug for source, _ in rows}
    missing = [slug for slug in slugs if slug not in found]
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={
                "error": "unknown_source",
                "message": f"No registered source named: {', '.join(missing)}.",
                "remedy": (
                    "Seed the registry with `python -m contour_api.seeds.sources`, "
                    "or name a source that exists."
                ),
            },
        )

    terms: list[SourceTerms] = []
    for source, licence in rows:
        attribution = await session.scalar(
            select(SourceAttribution).where(SourceAttribution.source_id == source.id)
        )
        terms.append(
            SourceTerms(
                source_slug=source.slug,
                source_name=source.name,
                licence_identifier=licence.identifier if licence else "unverified",
                licence_name=licence.name if licence else "terms not verified",
                redistribution=(
                    licence.redistribution if licence else RedistributionPermission.UNKNOWN
                ),
                attribution_text=attribution.text if attribution else None,
                attribution_url=attribution.url if attribution else None,
                share_alike_required=bool(licence and licence.share_alike_required),
                commercial_use_permitted=(licence.commercial_use_permitted if licence else None),
                restriction_summary=(
                    licence.restriction_summary
                    if licence
                    else (
                        source.redistribution_restrictions
                        or "The licence terms for this source have never been read."
                    )
                ),
            )
        )
    return terms


def _refused(decision) -> HTTPException:
    """A refusal that says which source, on what terms, and what would change it."""
    return HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail={"error": "export_not_permitted", **decision.as_dict()},
    )


# --- endpoints ---------------------------------------------------------------


@router.get(
    "/exports/formats",
    summary="Which formats Contour can write, and which it cannot",
)
async def formats() -> dict:
    return {
        "supported": list(SUPPORTED_EXPORT_FORMATS),
        "unsupported": [
            {
                "format": "fit",
                "reason": (
                    "The FIT specification has not been verified in this "
                    "deployment, and Contour does not write a format it has not "
                    "read the specification for."
                ),
            },
            {
                "format": "tcx",
                "reason": "Not implemented. TCX is a training format; its route support is limited.",
            },
        ],
        "note": (
            "Elevation is written only where it was measured. A point with no "
            "coverage is written without an elevation rather than at zero."
        ),
    }


@router.post(
    "/exports",
    summary="Export a route, subject to every contributing source's licence",
    description=(
        "The licence check runs before anything is written, so a refused export "
        "never exists as a file. A permitted export carries an attribution "
        "manifest naming every source that contributed."
    ),
    responses={403: {"description": "A contributing source's licence forbids export."}},
)
async def export_route(body: ExportRequest, session: SessionDep) -> Response:
    if body.format not in SUPPORTED_EXPORT_FORMATS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={
                "error": "unsupported_format",
                "message": f"Contour cannot write '{body.format}'.",
                "supported": list(SUPPORTED_EXPORT_FORMATS),
            },
        )

    terms = await load_terms(session, body.source_slugs)
    action = Action.COMMERCIAL_USE if body.commercial_use else Action.EXPORT
    decision = evaluate(action, terms)
    if not decision.allowed:
        raise _refused(decision)

    manifest = AttributionManifest(sources=list(decision.required_attributions) or terms)

    if body.format == "geojson":
        return Response(
            content=_geojson(body, manifest),
            media_type="application/geo+json",
            headers=_headers(body.name, "geojson", decision),
        )

    track = ParsedTrack(
        name=body.name,
        description=_description(body, manifest),
        points=tuple(
            TrackPoint(lat=p.lat, lon=p.lon, elevation_m=p.elevation_m, time=None)
            for p in body.points
        ),
    )
    return Response(
        content=write_gpx(track, as_route=body.format == "gpx-route"),
        media_type="application/gpx+xml",
        headers=_headers(body.name, "gpx", decision),
    )


def _description(body: ExportRequest, manifest: AttributionManifest) -> str:
    """The manifest travels inside the file, not only in the HTTP response.

    A GPX file outlives the request that produced it. Attribution that lived
    only in a header would be gone the moment the file was saved.
    """
    parts = [body.description] if body.description else []
    parts.append(manifest.render_text())
    return "\n\n".join(parts)


def _geojson(body: ExportRequest, manifest: AttributionManifest) -> str:
    import json

    return json.dumps(
        {
            "type": "Feature",
            "geometry": {
                "type": "LineString",
                # GeoJSON positions are [lon, lat, elevation]. A point with no
                # measured elevation is written as a 2D position rather than
                # padded with zero, which readers would take as sea level.
                "coordinates": [
                    [p.lon, p.lat, p.elevation_m] if p.elevation_m is not None else [p.lon, p.lat]
                    for p in body.points
                ],
            },
            "properties": {
                "name": body.name,
                "description": body.description,
                "attribution": manifest.as_dict(),
            },
        },
        indent=2,
    )


def _headers(name: str, extension: str, decision) -> dict[str, str]:
    # Every character outside a small safe set becomes a dash, then runs of
    # dashes collapse: a name that is entirely punctuation would otherwise
    # produce "---.gpx", which is a filename in form only.
    safe = "".join(c if c.isalnum() or c in "-_" else "-" for c in name)[:80]
    safe = "-".join(part for part in safe.split("-") if part) or "route"
    headers = {
        "content-disposition": f'attachment; filename="{safe}.{extension}"',
        # Read by clients that show the credit beside a download.
        "x-contour-attribution": "; ".join(
            source.attribution_text or source.source_name
            for source in decision.required_attributions
        )
        or "none required",
    }
    if decision.share_alike_required:
        headers["x-contour-share-alike"] = (
            "Derived works must be offered under the same licence as the source data."
        )
    return headers


@router.post(
    "/journeys/stages",
    summary="Divide a route into days within the rider's limits",
    description=(
        "Returns the stages that are achievable under the stated limits. Where "
        "no division satisfies them, the plan is returned as infeasible with the "
        "reason, rather than with the limits quietly relaxed."
    ),
)
async def stages(body: StageRequest) -> dict[str, Any]:
    points = [
        RoutePoint(
            distance_m=point.distance_m,
            ascent_from_start_m=point.ascent_from_start_m,
            name=point.name,
            is_overnight_candidate=point.is_overnight_candidate,
            has_accommodation=point.has_accommodation,
            has_water=point.has_water,
            has_food=point.has_food,
        )
        for point in sorted(body.points, key=lambda p: p.distance_m)
    ]

    constraints = StageConstraints(
        max_daily_distance_m=body.max_daily_distance_m,
        min_daily_distance_m=body.min_daily_distance_m,
        max_daily_ascent_m=body.max_daily_ascent_m,
        max_daily_riding_time_s=body.max_daily_riding_time_s,
        assumed_speed_mps=body.assumed_speed_mps,
        target_days=body.target_days,
        locked_end_distances_m=tuple(body.locked_end_distances_m),
    )

    plan = plan_stages(points, constraints, body.strategy)
    payload = plan.as_dict()
    payload["assumptions"] = {
        "assumed_speed_mps": body.assumed_speed_mps,
        "note": (
            "Riding time is derived from the assumed speed above, which is a "
            "setting rather than a prediction about this rider. Contour does not "
            "model how fast anyone rides."
        ),
    }
    return payload
