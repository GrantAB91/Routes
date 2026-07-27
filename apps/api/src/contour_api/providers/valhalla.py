"""Valhalla implementation of :class:`RoutingProvider`.

Contour targets Valhalla 3.8.3, built from source by ``infra/valhalla/build.sh``.
Every option name used here is quoted from the API reference shipped in that
checkout at ``docs/docs/api/route/api-reference.md`` and
``docs/docs/api/elevation.md``.

Three documented limits of the engine shape how this adapter is used, and are
recorded here because they are easy to forget and expensive to rediscover:

1. ``alternates`` is documented as "not yet supported on multipoint routes".
   Any route with via points therefore gets no engine alternatives at all, so
   Contour's distinct alternatives come from corridor-penalised re-solves in
   ``contour_api.routing.solver`` rather than from this parameter.
2. ``use_hills`` is a *preference* between 0 and 1 that penalises grade. It is
   not a gradient limit, and the documentation warns "it is not always possible
   to find alternate paths to avoid hills". A maximum-gradient requirement can
   only be enforced by validating the returned geometry (§7.6).
3. ``avoid_bad_surfaces`` at exactly 1.0 disallows bad surfaces "including start
   and end points", which turns a preference into an unroutable request when an
   endpoint sits on an unpaved lane. Contour clamps below 1.0 and enforces
   surface limits through validation instead.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import httpx

from ..models.enums import BicycleType
from .routing import (
    CostingPreferences,
    ExclusionSet,
    HealthReport,
    LatLon,
    NoRouteFoundError,
    ProviderHealth,
    RouteCandidate,
    RouteLegShape,
    RouteRequest,
    RoutingError,
)

# Valhalla's documented bicycle_type values. There is no e-bike type; Contour's
# EBIKE profile rides on `hybrid` and differs only in gradient preference, which
# is stated plainly rather than implying the engine models a battery (§7.2.6).
_BICYCLE_TYPE: dict[BicycleType, str] = {
    BicycleType.ROAD: "road",
    BicycleType.HYBRID: "hybrid",
    BicycleType.CROSS: "cross",
    BicycleType.MOUNTAIN: "mountain",
    BicycleType.EBIKE: "hybrid",
    BicycleType.CUSTOM: "hybrid",
}

# Documented defaults, restated so a Contour preference left unset produces the
# engine's own behaviour rather than an accidental Contour opinion.
_DEFAULT_USE_ROADS = 0.25
_DEFAULT_USE_HILLS = 0.25
_DEFAULT_USE_FERRY = 0.5
_DEFAULT_AVOID_BAD_SURFACES = 0.25

# See note 3 in the module docstring.
_MAX_AVOID_BAD_SURFACES = 0.95

# Valhalla's `service_limits.trace` defaults, which infra/valhalla/make-config.sh
# leaves alone. They are an order of magnitude tighter than the bicycle routing
# limits, so a route the engine will happily compute is one it will refuse to
# trace in a single call.
_TRACE_MAX_SHAPE = 16_000
_TRACE_MAX_DISTANCE_M = 200_000.0

# Kept below the server's ceilings rather than at them: the limits are compared
# against the shape as the engine measures it, and a chunk sized exactly to the
# limit can tip over on rounding.
_TRACE_SHAPE_SAFETY = 0.9

# How many times a failing stretch is halved before the remainder is left
# unattributed. Eight halvings take a 90 km leg down to roughly 350 m, which is
# a small enough gap to report honestly and not worth further round trips.
_TRACE_MAX_BISECTIONS = 8


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


@dataclass(frozen=True, slots=True)
class TracedEdge:
    """One edge of a routed shape, as ``/trace_attributes`` reported it.

    ``way_id`` is the point of the whole exercise: it is the join key onto
    Contour's imported network, and therefore the only route by which a
    generated geometry acquires attributes from a named source.

    The ``engine_*`` fields are kept deliberately prefixed. They are the
    engine's own reading of the same underlying data, and they are *evidence*
    about how the route was costed — never a source. Contour does not present
    them as attributes: ``Surface`` is documented upstream as a "Generalized
    representation", and ``CycleLane::kNone`` means "no specified bicycle lane",
    which conflates surveyed-absent with never-surveyed. Substituting either for
    a real observation would manufacture exactly the certainty §2.6 forbids.
    """

    way_id: int | None
    graph_edge_id: str | None
    distance_m: float
    begin_shape_index: int
    end_shape_index: int
    use: str | None = None
    engine_road_class: str | None = None
    engine_surface: str | None = None
    engine_cycle_lane: str | None = None
    engine_speed_limit: int | None = None

    @property
    def is_ferry_by_engine(self) -> bool:
        """Whether the engine costed this edge as a ferry.

        Structural rather than attributive: it says which part of the graph the
        route used, which is why it is safe to read where a surface value is not.
        """
        return self.use in {"ferry", "rail-ferry"}


def _chunk_shape(
    coordinates: tuple[LatLon, ...],
    max_shape: int,
    max_chunk_m: float,
) -> list[tuple[int, tuple[LatLon, ...]]]:
    """Split a shape into overlapping windows within the trace limits.

    Consecutive windows share their boundary point. Without the overlap the edge
    spanning a boundary is dropped from both windows, leaving an unattributed
    hole every 200 km — which would read as genuinely unsurveyed road rather
    than as a request that was chopped up.
    """
    # Imported here rather than at module scope: routing.validation imports
    # from providers.routing, so a top-level import would close a cycle.
    from ..routing.validation import haversine_m

    limit = max(2, int(max_shape * _TRACE_SHAPE_SAFETY))
    budget = max_chunk_m * _TRACE_SHAPE_SAFETY

    chunks: list[tuple[int, tuple[LatLon, ...]]] = []
    start = 0
    cursor = 0.0

    for index in range(1, len(coordinates)):
        cursor += haversine_m(coordinates[index - 1], coordinates[index])
        too_long = cursor >= budget
        too_many = index - start + 1 >= limit
        if too_long or too_many:
            chunks.append((start, coordinates[start : index + 1]))
            # Restart *at* this point, not after it, so the shared vertex keeps
            # the two windows contiguous.
            start = index
            cursor = 0.0

    if start < len(coordinates) - 1:
        chunks.append((start, coordinates[start:]))

    return chunks


class ValhallaProvider:
    """Talks to a ``valhalla_service`` HTTP endpoint."""

    name = "valhalla"

    def __init__(
        self,
        base_url: str,
        *,
        client: httpx.AsyncClient | None = None,
        timeout_s: float = 60.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._client = client
        self._timeout_s = timeout_s
        self._owns_client = client is None

    async def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout_s)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None and self._owns_client:
            await self._client.aclose()
            self._client = None

    # -- health -------------------------------------------------------------

    async def health(self) -> HealthReport:
        """Query ``/status``.

        ``verbose=true`` returns the tile set's version and bounding box, which
        Contour records against generated routes so a result stays explainable
        after a tile rebuild.
        """
        try:
            client = await self._http()
            response = await client.get(
                f"{self._base_url}/status", params={"verbose": "true"}, timeout=10.0
            )
            response.raise_for_status()
            payload = response.json()
        except httpx.HTTPError as exc:
            return HealthReport(
                status=ProviderHealth.UNAVAILABLE,
                detail=f"Valhalla at {self._base_url} did not respond: {exc}",
            )

        return HealthReport(
            status=ProviderHealth.HEALTHY,
            detail="Valhalla responded to /status",
            version=payload.get("version"),
            dataset_version=payload.get("tileset_last_modified")
            and str(payload["tileset_last_modified"]),
        )

    # -- costing ------------------------------------------------------------

    def build_costing_options(self, preferences: CostingPreferences) -> dict[str, Any]:
        """Map Contour preferences onto Valhalla's documented bicycle options.

        Contour deliberately exposes preferences the engine does not have
        one-to-one options for. Where that happens the mapping is stated rather
        than silently dropped:

        * ``prefer_cycle_infrastructure`` has no dedicated option. Valhalla
          biases toward cycleways through a *low* ``use_roads``, so the
          preference is inverted onto that axis.
        * ``prefer_paved`` and ``prefer_gravel`` both act on
          ``avoid_bad_surfaces``, whose meaning is relative to ``bicycle_type``.
          Neither can express "only surfaces known to be paved", because the
          engine cannot distinguish unknown from smooth. That distinction is
          enforced in validation, where the data is available.
        """
        options: dict[str, Any] = {
            "bicycle_type": _BICYCLE_TYPE[preferences.bicycle_type],
        }

        if preferences.use_roads is not None:
            options["use_roads"] = _clamp(preferences.use_roads)
        elif preferences.prefer_cycle_infrastructure is not None:
            options["use_roads"] = _clamp(1.0 - preferences.prefer_cycle_infrastructure)
        else:
            options["use_roads"] = _DEFAULT_USE_ROADS

        if preferences.avoid_hills is not None:
            # Contour states the preference as "avoid hills"; Valhalla states it
            # as "willingness to tackle hills". Inverted here, once.
            options["use_hills"] = _clamp(1.0 - preferences.avoid_hills)
        else:
            options["use_hills"] = _DEFAULT_USE_HILLS

        options["use_ferry"] = (
            _clamp(preferences.use_ferry)
            if preferences.use_ferry is not None
            else _DEFAULT_USE_FERRY
        )
        if preferences.use_living_streets is not None:
            options["use_living_streets"] = _clamp(preferences.use_living_streets)

        if preferences.avoid_bad_surfaces is not None:
            bad_surfaces = preferences.avoid_bad_surfaces
        elif preferences.prefer_paved is not None:
            bad_surfaces = preferences.prefer_paved
        elif preferences.prefer_gravel is not None:
            # Wanting gravel is not the same as tolerating bad surfaces, but it
            # is the only axis the engine offers; a low value stops the engine
            # steering away from unsealed roads.
            bad_surfaces = 0.0
        else:
            bad_surfaces = _DEFAULT_AVOID_BAD_SURFACES
        options["avoid_bad_surfaces"] = _clamp(bad_surfaces, 0.0, _MAX_AVOID_BAD_SURFACES)

        if preferences.shortest:
            # Documented to disable all other costings and penalties, so it is
            # never combined silently with the preferences above.
            options = {"bicycle_type": options["bicycle_type"], "shortest": True}

        return options

    @staticmethod
    def _exclusions_payload(exclusions: ExclusionSet) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if exclusions.locations:
            payload["exclude_locations"] = [
                {"lat": p.lat, "lon": p.lon} for p in exclusions.locations
            ]
        if exclusions.polygons:
            # Documented as exterior rings of [lon, lat] pairs — note the order
            # is the reverse of the `locations` objects above.
            payload["exclude_polygons"] = [
                [[p.lon, p.lat] for p in ring] for ring in exclusions.polygons
            ]
        return payload

    # -- routing ------------------------------------------------------------

    async def route(self, request: RouteRequest) -> list[RouteCandidate]:
        payload: dict[str, Any] = {
            "locations": [
                {
                    "lat": loc.point.lat,
                    "lon": loc.point.lon,
                    "type": loc.kind,
                    **({"heading": loc.heading_deg} if loc.heading_deg is not None else {}),
                    **({"name": loc.name} if loc.name else {}),
                }
                for loc in request.locations
            ],
            "costing": "bicycle",
            "costing_options": {"bicycle": self.build_costing_options(request.preferences)},
            "directions_options": {"units": "kilometers", "language": request.language},
        }
        payload.update(self._exclusions_payload(request.exclusions))

        # Only ask for alternates where the engine supports them. Requesting
        # them on a multipoint route is silently ignored upstream, which would
        # leave the solver believing it had asked for variety it never got.
        if request.alternates > 0 and len(request.locations) == 2:
            payload["alternates"] = request.alternates

        data = await self._post("/route", payload)
        trip = data.get("trip")
        if not trip:
            raise RoutingError("malformed_response", "Valhalla returned no trip")

        candidates = [self._parse_trip(trip, data)]
        for alternate in data.get("alternates") or []:
            alt_trip = alternate.get("trip")
            if alt_trip:
                candidates.append(self._parse_trip(alt_trip, alternate))
        return candidates

    async def map_match(
        self, points: tuple[LatLon, ...], preferences: CostingPreferences
    ) -> RouteCandidate:
        payload: dict[str, Any] = {
            "shape": [{"lat": p.lat, "lon": p.lon} for p in points],
            "costing": "bicycle",
            "costing_options": {"bicycle": self.build_costing_options(preferences)},
            "shape_match": "map_snap",
            "directions_options": {"units": "kilometers"},
        }
        data = await self._post("/trace_route", payload)
        trip = data.get("trip")
        if not trip:
            raise RoutingError("malformed_response", "Valhalla returned no matched trip")
        return self._parse_trip(trip, data)

    async def trace_edges(
        self,
        coordinates: tuple[LatLon, ...],
        preferences: CostingPreferences,
        *,
        max_shape: int = _TRACE_MAX_SHAPE,
        max_chunk_m: float = _TRACE_MAX_DISTANCE_M,
        index_offset: int = 0,
    ) -> list[TracedEdge]:
        """Recover per-edge identity for a shape the engine itself produced.

        ``/route`` does not report which OSM way each part of a route came from,
        so a returned geometry carries no handle onto Contour's own attributes.
        ``/trace_attributes`` does report it, as ``edge.way_id``, and with
        ``shape_match=edge_walk`` it walks the exact edges rather than
        re-matching — which is correct here precisely because the shape came
        from a prior Valhalla route (the engine's own words: "this algorithm
        requires nearly exact shape matching, so it should only be used when the
        shape is from a prior Valhalla route").

        The call is chunked because trace has its own service limits, far tighter
        than routing's: ``trace.max_distance`` is 200 km against bicycle's
        1,000 km, and ``trace.max_shape`` is 16,000 points. The Wild Atlantic Way
        is roughly 2,500 km, so an unchunked call would be rejected outright —
        or worse, on a differently configured server, silently truncated.

        ``edge_walk`` is used without a map-matching fallback on purpose. If the
        walk fails, the honest outcome is fewer attributed edges, not a snapped
        approximation of a route the engine already computed exactly; snapping
        would attribute segments from ways the route does not actually use.

        ``index_offset`` shifts every returned shape index, so a caller tracing
        one leg of a multi-leg route gets indices into the whole route's shape.
        :meth:`trace_candidate` is what callers should normally use.
        """
        if len(coordinates) < 2:
            return []

        attributes = [
            "edge.way_id",
            "edge.id",
            "edge.length",
            "edge.begin_shape_index",
            "edge.end_shape_index",
            "edge.use",
            "edge.road_class",
            "edge.surface",
            "edge.cycle_lane",
            "edge.speed_limit",
            "edge.travel_mode",
        ]

        traced: list[TracedEdge] = []
        for chunk_start, chunk in _chunk_shape(coordinates, max_shape, max_chunk_m):
            traced.extend(
                await self._walk(chunk, chunk_start + index_offset, preferences, attributes)
            )

        return traced

    async def _walk(
        self,
        shape: tuple[LatLon, ...],
        offset: int,
        preferences: CostingPreferences,
        attributes: list[str],
        *,
        depth: int = 0,
    ) -> list[TracedEdge]:
        """Walk one stretch of shape, halving it if the walk cannot complete.

        ``edge_walk`` is all-or-nothing per request: one unwalkable spot returns
        443 for the whole thing. On a 287 km Wild Atlantic Way section that cost
        an entire 90 km leg — 31% of the route unattributed because of a single
        point somewhere in it.

        Splitting on failure recovers everything that *is* walkable and isolates
        what is not to a progressively smaller stretch. The alternative upstream
        offers is ``walk_or_snap``, which is declined deliberately: snapping
        invents a path through edges the route may never have used, and every
        attribute Contour then reported would be about the wrong road. Leaving a
        short stretch unattributed is a gap the route reports; snapping it is a
        fabrication the route cannot detect.
        """
        if len(shape) < 2:
            return []

        payload: dict[str, Any] = {
            "shape": [{"lat": p.lat, "lon": p.lon} for p in shape],
            "costing": "bicycle",
            "costing_options": {"bicycle": self.build_costing_options(preferences)},
            "shape_match": "edge_walk",
            "filters": {"attributes": attributes, "action": "include"},
            "directions_options": {"units": "kilometers"},
        }

        try:
            data = await self._post("/trace_attributes", payload)
        except NoRouteFoundError:
            # Below this the halves are too short to be worth another round
            # trip, and whatever is left unattributed is a few tens of metres.
            if depth >= _TRACE_MAX_BISECTIONS or len(shape) < 8:
                return []

            middle = len(shape) // 2
            # The halves share their boundary vertex, for the same reason the
            # chunks do: without it the edge spanning the split is lost twice.
            head = await self._walk(
                shape[: middle + 1], offset, preferences, attributes, depth=depth + 1
            )
            tail = await self._walk(
                shape[middle:], offset + middle, preferences, attributes, depth=depth + 1
            )
            return head + tail

        # Indices are into the shape trace was given, so offsetting by where
        # that shape starts keeps them pointing at the whole route.
        return [
            TracedEdge(
                way_id=int(edge["way_id"]) if edge.get("way_id") is not None else None,
                graph_edge_id=str(edge["id"]) if edge.get("id") is not None else None,
                distance_m=float(edge.get("length", 0.0)) * 1000.0,
                begin_shape_index=offset + int(edge.get("begin_shape_index", 0)),
                end_shape_index=offset + int(edge.get("end_shape_index", 0)),
                use=edge.get("use"),
                engine_road_class=edge.get("road_class"),
                engine_surface=edge.get("surface"),
                engine_cycle_lane=edge.get("cycle_lane"),
                engine_speed_limit=edge.get("speed_limit"),
            )
            for edge in data.get("edges") or []
        ]

    async def trace_candidate(
        self,
        candidate: RouteCandidate,
        preferences: CostingPreferences,
    ) -> list[TracedEdge]:
        """Recover edge identity for a whole route, one leg at a time.

        Legs are traced separately because the concatenated shape is not a path
        the engine can walk. At a ``break`` location the route is permitted to
        turn around, so the joined line can double back on itself, and
        ``edge_walk`` — which follows connected edges — then fails on the entire
        request rather than on the part that doubles back.

        This was not theoretical. A 287 km, five-leg Wild Atlantic Way section
        returned *zero* attributed segments while the same code attributed a
        single-leg 22 km route perfectly. The validator caught it and reported
        zero length with a NOT_FEASIBLE verdict rather than a confident route
        with no attributes, but the cause was here.

        Indices are offset per leg so they address the route's own shape — what
        :attr:`RouteCandidate.coordinates` returns, and what the attributor
        slices to give each segment its geometry.
        """
        offset = 0
        traced: list[TracedEdge] = []

        for index, leg in enumerate(candidate.legs):
            if len(leg.coordinates) >= 2:
                traced.extend(
                    await self.trace_edges(leg.coordinates, preferences, index_offset=offset)
                )

            # `RouteCandidate.coordinates` drops a leg's first point when it
            # repeats the previous leg's last. The offset has to be computed the
            # same way, or every index after the first leg is out by one.
            advance = len(leg.coordinates)
            previous = candidate.legs[index - 1].coordinates if index > 0 else ()
            if previous and leg.coordinates and previous[-1] == leg.coordinates[0]:
                advance -= 1
            offset += advance

        return traced

    async def heights(
        self, points: tuple[LatLon, ...], *, with_range: bool = True
    ) -> list[tuple[float, float | None]]:
        """Sample elevation along ``points`` via ``/height``.

        ``height_precision`` is set to 2 rather than left at its default of 0.
        The documentation notes that integer precision produces "stair step"
        changes along a nearly flat road; those artificial steps are counted as
        real climbing by any cumulative-ascent calculation, which systematically
        inflates the ascent of flat routes.

        Requesting two decimals removes the quantisation artefact from the
        *calculation*. It says nothing about the accuracy of the underlying DEM,
        and Contour never displays elevation to a precision the dataset's
        published accuracy does not support (§11.3, docs/elevation_method.md).

        Returns ``(distance_m, elevation_m)`` pairs. A null height is preserved
        as ``None`` — a gap in the DEM, never interpolated away.
        """
        payload: dict[str, Any] = {
            "shape": [{"lat": p.lat, "lon": p.lon} for p in points],
            "height_precision": 2,
            "range": with_range,
        }
        data = await self._post("/height", payload)

        if with_range:
            pairs = data.get("range_height") or []
            return [(float(item[0]), None if item[1] is None else float(item[1])) for item in pairs]
        heights = data.get("height") or []
        return [(0.0, None if h is None else float(h)) for h in heights]

    # -- transport ----------------------------------------------------------

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        client = await self._http()
        try:
            response = await client.post(f"{self._base_url}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise RoutingError(
                "provider_unreachable",
                f"Valhalla at {self._base_url} could not be reached: {exc}",
                retryable=True,
            ) from exc

        if response.status_code >= 400:
            raise self._translate_error(response)
        try:
            return response.json()
        except json.JSONDecodeError as exc:
            raise RoutingError("malformed_response", "Valhalla returned invalid JSON") from exc

    @staticmethod
    def _translate_error(response: httpx.Response) -> RoutingError:
        """Turn a Valhalla error into a Contour error code.

        Valhalla's own error codes are preserved in the message so an operator
        can match a user report against the engine log, rather than being
        flattened into a generic failure (§16.7).
        """
        try:
            body = response.json()
        except json.JSONDecodeError:
            body = {}

        code = body.get("error_code")
        message = body.get("error") or response.text or "unknown Valhalla error"

        # 442 is Valhalla's "no path could be found for input" family.
        if code in {442, 443} or "no path" in message.lower():
            return NoRouteFoundError(f"Valhalla could not connect the locations: {message}")
        if response.status_code >= 500:
            return RoutingError("provider_error", f"Valhalla failed: {message}", retryable=True)
        return RoutingError("invalid_routing_request", f"Valhalla rejected the request: {message}")

    # -- parsing ------------------------------------------------------------

    def _parse_trip(self, trip: dict[str, Any], envelope: dict[str, Any]) -> RouteCandidate:
        legs: list[RouteLegShape] = []
        for leg in trip.get("legs") or []:
            shape = leg.get("shape")
            coordinates = tuple(decode_polyline6(shape)) if shape else ()
            summary = leg.get("summary") or {}
            legs.append(
                RouteLegShape(
                    coordinates=coordinates,
                    # Valhalla is asked for kilometres above; Contour works in
                    # metres everywhere so the conversion happens once, here.
                    distance_m=float(summary.get("length", 0.0)) * 1000.0,
                    duration_s=(
                        float(summary["time"]) if summary.get("time") is not None else None
                    ),
                )
            )

        summary = trip.get("summary") or {}
        return RouteCandidate(
            legs=tuple(legs),
            distance_m=float(summary.get("length", 0.0)) * 1000.0,
            duration_s=float(summary["time"]) if summary.get("time") is not None else None,
            provider=self.name,
            engine_version=envelope.get("version") or trip.get("version"),
            raw_response=envelope,
        )


def decode_polyline6(encoded: str) -> list[LatLon]:
    """Decode a Valhalla shape string.

    Valhalla encodes route shapes with six decimal digits of precision rather
    than the five used by the original Google polyline algorithm; decoding with
    the wrong precision silently yields coordinates off by a factor of ten,
    which looks like a routing bug rather than a decoding one.
    """
    return _decode_polyline(encoded, 1e6)


def decode_polyline5(encoded: str) -> list[LatLon]:
    return _decode_polyline(encoded, 1e5)


def _decode_polyline(encoded: str, precision: float) -> list[LatLon]:
    coordinates: list[LatLon] = []
    index = 0
    lat = 0
    lon = 0
    length = len(encoded)

    while index < length:
        for axis in ("lat", "lon"):
            result = 0
            shift = 0
            while True:
                if index >= length:
                    raise ValueError("truncated polyline")
                byte = ord(encoded[index]) - 63
                index += 1
                result |= (byte & 0x1F) << shift
                shift += 5
                if byte < 0x20:
                    break
            delta = ~(result >> 1) if result & 1 else (result >> 1)
            if axis == "lat":
                lat += delta
            else:
                lon += delta
        coordinates.append(LatLon(lat=lat / precision, lon=lon / precision))

    return coordinates
