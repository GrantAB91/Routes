"""The routing provider seam (§5.5).

Contour separates three responsibilities that are easy to conflate:

* a **routing provider** turns locations and a cost model into geometry. It is
  deterministic and it is the only thing permitted to produce geometry (§7.1);
* the **constraint validator** decides whether that geometry satisfies what the
  user asked for. It lives in ``contour_api.routing.validation``, not here;
* the **solve loop** in ``contour_api.routing.solver`` mediates between the two.

The split exists because no routing engine expresses every constraint in §7.4.
Valhalla's bicycle costing has no notion of "maximum gradient", "maximum
unpaved distance" or "maximum unknown-surface distance", so those can only be
checked after geometry exists. Putting the check behind this seam would hide
that; keeping it outside means an engine swap changes how routes are *found*
and never changes what counts as compliant.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Protocol, runtime_checkable

from ..models.enums import BicycleType


class ProviderHealth(StrEnum):
    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    DISABLED = "disabled"


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Health of one provider.

    ``detail`` is user-facing. A degraded or unavailable provider produces an
    explicit limited state rather than a substituted result (§21.8), so this
    text ends up in front of a person and must say what is actually wrong.
    """

    status: ProviderHealth
    detail: str
    version: str | None = None
    dataset_version: str | None = None


@dataclass(frozen=True, slots=True)
class LatLon:
    lat: float
    lon: float

    def __post_init__(self) -> None:
        if not -90.0 <= self.lat <= 90.0:
            raise ValueError(f"latitude out of range: {self.lat}")
        if not -180.0 <= self.lon <= 180.0:
            raise ValueError(f"longitude out of range: {self.lon}")


@dataclass(frozen=True, slots=True)
class RoutingLocation:
    point: LatLon
    # "break" separates legs and permits a u-turn; "through" forces the route to
    # pass without breaking. Locked via points use "through" so that a re-solve
    # cannot quietly reorder them.
    kind: str = "break"
    heading_deg: int | None = None
    name: str | None = None


@dataclass(frozen=True, slots=True)
class CostingPreferences:
    """Soft preferences handed to the engine (§7.5).

    These bias the search. They are not guarantees, and nothing downstream may
    report a preference as satisfied without checking the resulting geometry.
    Values are normalised 0..1 in Contour's own vocabulary; each provider maps
    them onto its native options, which is why no Valhalla option name appears
    in this module.
    """

    bicycle_type: BicycleType = BicycleType.HYBRID
    avoid_hills: float | None = None
    avoid_bad_surfaces: float | None = None
    use_roads: float | None = None
    use_ferry: float | None = None
    use_living_streets: float | None = None
    prefer_cycle_infrastructure: float | None = None
    # Applied only where a segment's surface is actually known. An unknown
    # surface is never treated as satisfying a "prefer paved" preference.
    prefer_paved: float | None = None
    prefer_gravel: float | None = None
    shortest: bool = False


@dataclass(frozen=True, slots=True)
class ExclusionSet:
    """Everything the solve loop wants the engine to keep away from.

    Populated from user avoid areas and, on each re-solve pass, from the
    segments the validator found in violation. Growing this set is how the loop
    converges without ever editing geometry by hand.
    """

    polygons: tuple[tuple[LatLon, ...], ...] = ()
    locations: tuple[LatLon, ...] = ()
    edge_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteRequest:
    locations: tuple[RoutingLocation, ...]
    preferences: CostingPreferences = field(default_factory=CostingPreferences)
    exclusions: ExclusionSet = field(default_factory=ExclusionSet)
    # Ask the engine for its own alternatives. Contour additionally generates
    # corridor-penalised alternatives in the solver, because engine alternates
    # are often near-identical and §7.8 requires meaningful difference.
    alternates: int = 0
    language: str = "en"

    def __post_init__(self) -> None:
        if len(self.locations) < 2:
            raise ValueError("a route needs at least an origin and a destination")


@dataclass(frozen=True, slots=True)
class RouteLegShape:
    """Geometry and per-edge attributes for one leg, as the engine reported them.

    ``edges`` holds the engine's own attribution, kept separate from Contour's
    stored segment attributes. The engine's view is evidence about how the route
    was costed; it is not a source, and it never overwrites what an import
    recorded from a real publisher.
    """

    coordinates: tuple[LatLon, ...]
    distance_m: float
    duration_s: float | None
    edges: tuple[dict, ...] = ()


@dataclass(frozen=True, slots=True)
class RouteCandidate:
    legs: tuple[RouteLegShape, ...]
    distance_m: float
    duration_s: float | None
    provider: str
    engine_version: str | None = None
    tile_version: str | None = None
    raw_response: dict | None = None

    @property
    def coordinates(self) -> tuple[LatLon, ...]:
        points: list[LatLon] = []
        for leg in self.legs:
            if points and leg.coordinates and points[-1] == leg.coordinates[0]:
                points.extend(leg.coordinates[1:])
            else:
                points.extend(leg.coordinates)
        return tuple(points)


class RoutingError(Exception):
    """A routing failure with a stable, user-facing code.

    Errors carry a code so the API can explain what went wrong precisely rather
    than behind a generic message (§16.7, §19.4). ``retryable`` distinguishes a
    transient engine problem from a request that will never succeed.
    """

    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        self.code = code
        self.retryable = retryable
        super().__init__(message)


class NoRouteFound(RoutingError):
    """The engine could not connect the requested locations at all.

    Distinct from a route that exists but violates a constraint: that is a
    validation outcome with a feasibility verdict, not an error.
    """

    def __init__(self, message: str = "no route exists between the given locations") -> None:
        super().__init__("no_route_found", message)


@runtime_checkable
class RoutingProvider(Protocol):
    """A deterministic source of route geometry.

    Implementations must not invent geometry, must be deterministic for a given
    request and dataset, and must report their engine and tile versions so a
    stored route stays explainable after an upgrade.
    """

    name: str

    async def health(self) -> HealthReport: ...

    async def route(self, request: RouteRequest) -> list[RouteCandidate]:
        """Return one or more candidates, best first.

        Raises :class:`NoRouteFound` when no connection exists, and
        :class:`RoutingError` for engine failures. Returning an empty list is
        not permitted: it is indistinguishable from "no opinion".
        """
        ...

    async def map_match(
        self, points: tuple[LatLon, ...], preferences: CostingPreferences
    ) -> RouteCandidate:
        """Snap a recorded track onto the network.

        The result is stored beside the original, never in place of it (§6.8).
        """
        ...
