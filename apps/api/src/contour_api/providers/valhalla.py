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
from typing import Any

import httpx

from ..models.enums import BicycleType
from .routing import (
    CostingPreferences,
    ExclusionSet,
    HealthReport,
    LatLon,
    NoRouteFound,
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


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


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
            dataset_version=payload.get("tileset_last_modified") and str(
                payload["tileset_last_modified"]
            ),
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
            _clamp(preferences.use_ferry) if preferences.use_ferry is not None
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
            return [
                (float(item[0]), None if item[1] is None else float(item[1]))
                for item in pairs
            ]
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
            return NoRouteFound(f"Valhalla could not connect the locations: {message}")
        if response.status_code >= 500:
            return RoutingError(
                "provider_error", f"Valhalla failed: {message}", retryable=True
            )
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
