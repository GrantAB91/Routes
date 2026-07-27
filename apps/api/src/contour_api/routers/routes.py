"""Route generation, validation and comparison (§7, §16.7).

The endpoints here expose the solve loop. What they do *not* do is decide what
counts as an acceptable answer: the four-state verdict, the per-constraint
outcomes and the standing limitations all reach the client exactly as the
validator produced them, because §7.6 forbids quietly dropping a requirement and
a router that flattened them into 200-or-error would do precisely that.

A route that fails its constraints is still a 200. It arrives with
``NOT_FEASIBLE``, the exact violating segments, and how many attempts were made
— which is a useful answer, and more useful than an error. The status codes are
reserved for requests Contour could not process at all, and for capabilities
that are switched off, which are reported with the setting that would enable
them rather than as failures (§21.8).
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy.ext.asyncio import AsyncSession

from ..analysis.route_profile import RouteElevation, attach_elevation
from ..config import DisabledCapabilityError
from ..db import get_session
from ..models.enums import BicycleType, RoadClass, SurfaceFamily
from ..providers.factory import elevation_provider, routing_provider
from ..providers.routing import (
    CostingPreferences,
    ExclusionSet,
    LatLon,
    NoRouteFoundError,
    RouteRequest,
    RoutingError,
    RoutingLocation,
)
from ..routing.attribution import PostgisSegmentAttributor
from ..routing.constraints import (
    ConstraintSet,
    FerryUse,
    LegalBicycleAccess,
    MaxGradient,
    MaxSurfaceDistance,
    MaxTotalDistance,
    MaxUnknownSurfaceDistance,
    ProhibitedRoadClasses,
)
from ..routing.model import RouteView
from ..routing.solver import (
    AlternativeGenerator,
    RouteSolver,
    SolveResult,
    wild_atlantic_way_profiles,
)

router = APIRouter(prefix="/v1/routes", tags=["routes"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


# --- request models ----------------------------------------------------------


class Point(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)
    name: str | None = None
    # "break" permits a u-turn and separates legs; "through" forces the route to
    # pass without breaking, which is what a locked via point needs so a
    # re-solve cannot quietly reorder it.
    kind: str = Field(default="break", pattern="^(break|through|via|break_through)$")

    def to_location(self) -> RoutingLocation:
        return RoutingLocation(
            point=LatLon(lat=self.lat, lon=self.lon), kind=self.kind, name=self.name
        )


class Preferences(BaseModel):
    """Soft biases handed to the engine.

    Every field here influences the search and guarantees nothing. Anything that
    must hold of the result belongs in ``constraints``, which is checked against
    the geometry that comes back (§7.5).
    """

    bicycle_type: BicycleType = BicycleType.HYBRID
    avoid_hills: float | None = Field(default=None, ge=0, le=1)
    avoid_bad_surfaces: float | None = Field(default=None, ge=0, le=1)
    use_roads: float | None = Field(default=None, ge=0, le=1)
    use_ferry: float | None = Field(default=None, ge=0, le=1)
    prefer_cycle_infrastructure: float | None = Field(default=None, ge=0, le=1)
    prefer_paved: float | None = Field(default=None, ge=0, le=1)
    prefer_gravel: float | None = Field(default=None, ge=0, le=1)
    shortest: bool = False

    def to_costing(self) -> CostingPreferences:
        return CostingPreferences(
            bicycle_type=self.bicycle_type,
            avoid_hills=self.avoid_hills,
            avoid_bad_surfaces=self.avoid_bad_surfaces,
            use_roads=self.use_roads,
            use_ferry=self.use_ferry,
            prefer_cycle_infrastructure=self.prefer_cycle_infrastructure,
            prefer_paved=self.prefer_paved,
            prefer_gravel=self.prefer_gravel,
            shortest=self.shortest,
        )


class Constraints(BaseModel):
    """Requirements the returned geometry is checked against.

    Unset means unconstrained, not zero. ``max_unknown_surface_m`` in particular
    is the one that lets a rider say "some gravel is fine, a mystery is not" —
    and it is always evaluable, because how much is unknown is itself known.
    """

    max_gradient_percent: float | None = Field(default=None, gt=0, le=40)
    max_unpaved_m: float | None = Field(default=None, ge=0)
    max_unknown_surface_m: float | None = Field(default=None, ge=0)
    max_total_distance_m: float | None = Field(default=None, gt=0)
    require_legal_bicycle_access: bool = True
    prohibited_road_classes: list[RoadClass] = Field(default_factory=lambda: [RoadClass.MOTORWAY])
    ferries_allowed: bool = True
    ferries_required: bool = False

    def to_set(self) -> ConstraintSet:
        constraints: list[Any] = []
        if self.max_gradient_percent is not None:
            constraints.append(
                MaxGradient(
                    limit_percent=self.max_gradient_percent,
                    stated_as=f"no gradient above {self.max_gradient_percent:g}%",
                )
            )
        if self.max_unpaved_m is not None:
            constraints.append(
                MaxSurfaceDistance(
                    family=SurfaceFamily.UNPAVED,
                    limit_m=self.max_unpaved_m,
                    stated_as=f"at most {self.max_unpaved_m / 1000:g} km unpaved",
                )
            )
        if self.max_unknown_surface_m is not None:
            constraints.append(
                MaxUnknownSurfaceDistance(
                    limit_m=self.max_unknown_surface_m,
                    stated_as=(
                        f"at most {self.max_unknown_surface_m / 1000:g} km of unsurveyed surface"
                    ),
                )
            )
        if self.max_total_distance_m is not None:
            constraints.append(
                MaxTotalDistance(
                    limit_m=self.max_total_distance_m,
                    stated_as=f"no longer than {self.max_total_distance_m / 1000:g} km",
                )
            )
        if self.require_legal_bicycle_access:
            constraints.append(LegalBicycleAccess(stated_as="every segment must permit bicycles"))
        if self.prohibited_road_classes:
            constraints.append(
                ProhibitedRoadClasses(
                    classes=frozenset(self.prohibited_road_classes),
                    stated_as=("avoid " + ", ".join(c.value for c in self.prohibited_road_classes)),
                )
            )
        if not self.ferries_allowed or self.ferries_required:
            constraints.append(
                FerryUse(
                    allowed=self.ferries_allowed,
                    required=self.ferries_required,
                    stated_as=("no ferries" if not self.ferries_allowed else "must use a ferry"),
                )
            )
        return ConstraintSet(constraints=constraints)


class GenerateRequest(BaseModel):
    locations: list[Point] = Field(min_length=2, max_length=200)
    preferences: Preferences = Field(default_factory=Preferences)
    constraints: Constraints = Field(default_factory=Constraints)
    avoid_points: list[Point] = Field(default_factory=list, max_length=500)
    max_attempts: int = Field(default=4, ge=1, le=8)
    include_elevation: bool = True

    @model_validator(mode="after")
    def _distinct_endpoints(self) -> GenerateRequest:
        first, last = self.locations[0], self.locations[-1]
        if len(self.locations) == 2 and (first.lat, first.lon) == (last.lat, last.lon):
            raise ValueError(
                "origin and destination are the same point; a loop needs at least "
                "one via point to describe where it should go"
            )
        return self

    def to_route_request(self) -> RouteRequest:
        return RouteRequest(
            locations=tuple(point.to_location() for point in self.locations),
            preferences=self.preferences.to_costing(),
            exclusions=ExclusionSet(
                locations=tuple(LatLon(lat=p.lat, lon=p.lon) for p in self.avoid_points)
            ),
        )


class AlternativesRequest(GenerateRequest):
    max_alternatives: int = Field(default=4, ge=1, le=6)


class CompareRequest(BaseModel):
    """Two already-generated routes, compared on the same measurements."""

    routes: list[GenerateRequest] = Field(min_length=2, max_length=4)


# --- serialisation -----------------------------------------------------------


def _segment_payload(route: RouteView) -> list[dict]:
    return [
        {
            "index": s.index,
            "start_distance_m": round(s.start_distance_m, 1),
            "distance_m": round(s.distance_m, 1),
            "surface": {
                "family": s.surface_family.value,
                "status": s.surface_status.value,
            },
            "bicycle_access": {
                "value": s.bicycle_access.value,
                "status": s.bicycle_access_status.value,
            },
            "road_class": {
                "value": s.road_class.value,
                "status": s.road_class_status.value,
            },
            "cycle_lane": {
                "value": s.cycle_lane.value if s.cycle_lane else None,
                "status": s.cycle_lane_status.value,
            },
            "max_grade_percent": (
                round(s.max_grade_percent, 1) if s.max_grade_percent is not None else None
            ),
            "elevation_status": s.elevation_status.value,
            "is_ferry": s.is_ferry,
            "graph_edge_id": s.graph_edge_id,
        }
        for s in route.segments
    ]


def _route_payload(
    result: SolveResult,
    elevation: RouteElevation | None,
    *,
    include_geometry: bool = True,
) -> dict:
    route = result.route
    payload: dict[str, Any] = {
        "distance_m": round(route.distance_m, 1),
        "duration_s": result.candidate.duration_s,
        "validation": result.validation.as_dict(),
        "composition": {
            "surface_m": {k: round(v, 1) for k, v in route.surface_composition_m().items()},
            "road_class_m": {k: round(v, 1) for k, v in route.road_class_composition_m().items()},
            "cycle_infrastructure_m": {
                k: round(v, 1) for k, v in route.cycle_infrastructure_composition_m().items()
            },
        },
        "segments": _segment_payload(route),
        "attempts": [
            {
                "attempt": a.attempt,
                "distance_m": round(a.distance_m, 1),
                "verdict": a.verdict.value,
                "violating_segments": a.violating_segment_count,
                "violating_distance_m": round(a.violating_distance_m, 1),
                "excluded_locations": a.excluded_locations,
            }
            for a in result.attempts
        ],
        "provenance": route.metadata,
    }

    if include_geometry:
        payload["geometry"] = {
            "type": "LineString",
            "coordinates": [[p.lon, p.lat] for p in result.candidate.coordinates],
        }

    if elevation is not None:
        payload["elevation"] = elevation.as_dict()
    else:
        payload["elevation"] = None
        payload["elevation_note"] = (
            "No elevation source is configured, so this route has no profile and "
            "no gradient constraint could be checked. This is a deployment "
            "limitation, not a property of the route."
        )

    return payload


# --- assembly ----------------------------------------------------------------


async def _solve(
    session: AsyncSession,
    body: GenerateRequest,
) -> tuple[SolveResult, RouteElevation | None]:
    """Route, attribute, measure elevation, then validate against the result.

    Order matters. Validation runs last because the gradient constraint cannot
    be evaluated before the profile exists — validating first would report every
    gradient as unevaluated on a route that does have elevation.
    """
    provider = routing_provider()
    attributor = PostgisSegmentAttributor(
        session=session, provider=provider, preferences=body.preferences.to_costing()
    )
    solver = RouteSolver(provider=provider, attributor=attributor, max_attempts=body.max_attempts)
    constraint_set = body.constraints.to_set()

    try:
        result = await solver.solve(body.to_route_request(), constraint_set)
    finally:
        await provider.aclose()

    if not body.include_elevation:
        return result, None

    try:
        source = elevation_provider()
    except DisabledCapabilityError:
        # An explicit limited state, reported alongside the route rather than
        # failing it or silently omitting the profile (§21.8).
        return result, None

    measured = await attach_elevation(result.route, source)

    # Re-validate against the enriched route so the gradient constraint sees the
    # profile. Everything else is unchanged, so the outcome can only become more
    # informed, never less.
    from ..routing.validation import RouteValidator

    revalidated = RouteValidator(constraint_set).validate(
        measured.route,
        resolve_attempts=result.validation.resolve_attempts,
        exhausted_attempts=result.validation.exhausted_attempts,
    )

    from dataclasses import replace

    return replace(result, route=measured.route, validation=revalidated), measured


def _capability_error(exc: DisabledCapabilityError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail={"error": "capability_disabled", **exc.as_dict()},
    )


# --- endpoints ---------------------------------------------------------------


@router.post(
    "/generate",
    summary="Generate one route and validate it against the stated constraints",
    description=(
        "Returns the route together with the verdict, every constraint's outcome, "
        "and the attempts made. A route that cannot meet its constraints is "
        "returned with a NOT_FEASIBLE verdict and the exact violating segments "
        "rather than as an error, because that is the more useful answer."
    ),
)
async def generate(body: GenerateRequest, session: SessionDep) -> dict:
    try:
        result, elevation = await _solve(session, body)
    except DisabledCapabilityError as exc:
        raise _capability_error(exc) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={
                "error": "no_route_found",
                "message": str(exc),
                "remedy": (
                    "Check that the points are on or near a road the routing tiles "
                    "cover, and that any avoid points are not sealing off the only way through."
                ),
            },
        ) from exc
    except RoutingError as exc:
        raise HTTPException(
            status_code=(
                status.HTTP_502_BAD_GATEWAY if exc.retryable else status.HTTP_400_BAD_REQUEST
            ),
            detail={"error": exc.code, "message": str(exc), "retryable": exc.retryable},
        ) from exc

    return _route_payload(result, elevation)


@router.post(
    "/alternatives",
    summary="Generate meaningfully different alternatives",
    description=(
        "Solves the same journey under different preference profiles and discards "
        "near-copies, because two routes differing only in which side of a village "
        "they pass are not two alternatives. Each returned route states why it was "
        "selected and how much of it is unique."
    ),
)
async def alternatives(body: AlternativesRequest, session: SessionDep) -> dict:
    provider = routing_provider()
    attributor = PostgisSegmentAttributor(
        session=session, provider=provider, preferences=body.preferences.to_costing()
    )
    solver = RouteSolver(provider=provider, attributor=attributor, max_attempts=body.max_attempts)
    generator = AlternativeGenerator(solver=solver)
    constraint_set = body.constraints.to_set()

    try:
        produced = await generator.generate(
            body.to_route_request(),
            wild_atlantic_way_profiles()[: body.max_alternatives],
            lambda _profile: constraint_set,
        )
    except DisabledCapabilityError as exc:
        raise _capability_error(exc) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "no_route_found", "message": str(exc)},
        ) from exc
    finally:
        await provider.aclose()

    try:
        source = elevation_provider()
    except DisabledCapabilityError:
        source = None

    routes = []
    for alternative in produced:
        measured = await attach_elevation(alternative.result.route, source) if source else None
        routes.append(
            {
                "slug": alternative.profile.slug,
                "title": alternative.profile.title,
                "selection_reason": alternative.profile.selection_reason,
                "shared_distance_m": round(alternative.shared_distance_m, 1),
                "unique_distance_m": round(alternative.unique_distance_m, 1),
                "overlap_with": {
                    slug: round(value, 4) for slug, value in alternative.overlap_with.items()
                },
                **_route_payload(alternative.result, measured),
            }
        )

    return {
        "requested": body.max_alternatives,
        "returned": len(routes),
        "routes": routes,
        "note": (
            "Fewer routes than requested means the remaining profiles produced "
            "near-copies of one already listed, or could not be routed at all. "
            "Contour does not pad the list to make it look full."
        ),
    }


@router.post(
    "/validate",
    summary="Validate a route against constraints without returning its geometry",
    description=(
        "The same solve and the same verdict, with the geometry omitted. For "
        "checking whether a journey is feasible before committing to drawing it."
    ),
)
async def validate(body: GenerateRequest, session: SessionDep) -> dict:
    try:
        result, elevation = await _solve(session, body)
    except DisabledCapabilityError as exc:
        raise _capability_error(exc) from exc
    except NoRouteFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"error": "no_route_found", "message": str(exc)},
        ) from exc

    return _route_payload(result, elevation, include_geometry=False)


@router.post(
    "/compare",
    summary="Compare several routes on the same measurements",
    description=(
        "Every route is measured the same way, and differences that come from a "
        "gap in the data are labelled as such rather than presented as a "
        "difference between the routes."
    ),
)
async def compare(body: CompareRequest, session: SessionDep) -> dict:
    solved: list[dict] = []
    for index, request in enumerate(body.routes):
        try:
            result, elevation = await _solve(session, request)
        except DisabledCapabilityError as exc:
            raise _capability_error(exc) from exc
        except NoRouteFoundError as exc:
            solved.append(
                {
                    "index": index,
                    "routed": False,
                    "reason": str(exc),
                }
            )
            continue
        solved.append({"index": index, "routed": True, **_route_payload(result, elevation)})

    routed = [entry for entry in solved if entry.get("routed")]
    return {
        "routes": solved,
        "comparison": _comparison(routed),
    }


def _comparison(routed: list[dict]) -> dict:
    """Side-by-side metrics, with unknowns kept as unknowns.

    A route whose elevation could not be measured is not shown as having the
    least ascent. It is shown as not measured, which is why every row here
    carries its own null rather than a placeholder (§2.6, §14.4).
    """
    if not routed:
        return {"comparable": False, "reason": "no route was produced"}

    def ascent(entry: dict) -> float | None:
        elevation = entry.get("elevation")
        return elevation.get("ascent_m") if elevation else None

    ascents = {entry["index"]: ascent(entry) for entry in routed}
    measured = {index: value for index, value in ascents.items() if value is not None}

    return {
        "comparable": True,
        "distance_m": {entry["index"]: entry["distance_m"] for entry in routed},
        "ascent_m": ascents,
        "unpaved_m": {
            entry["index"]: entry["composition"]["surface_m"].get("unpaved") for entry in routed
        },
        "unknown_surface_m": {
            entry["index"]: entry["composition"]["surface_m"].get("unknown") for entry in routed
        },
        "verdict": {entry["index"]: entry["validation"]["verdict"] for entry in routed},
        "lowest_ascent_index": (min(measured, key=lambda k: measured[k]) if measured else None),
        "routes_without_elevation": [index for index, value in ascents.items() if value is None],
    }
