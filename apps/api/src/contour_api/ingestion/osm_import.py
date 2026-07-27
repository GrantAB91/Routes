"""Import the routable cycling network from an OpenStreetMap PBF extract.

This is the step that turns a file of surveyor observations into rows Contour
can defend. It does two things and refuses to do a third:

* it reads every routable way and records what the tags actually said, keeping
  the raw values alongside Contour's reading of them;
* it records what was *not* said, as ``UNKNOWN``, which is the majority case on
  rural Irish lanes and must survive all the way to the user (§2.6);
* it never fills a gap. There is no default surface, no assumed access, no
  inferred speed limit. A way with no ``surface`` tag arrives in the database
  unknown and leaves it unknown.

Structure is two passes over the extract, because the two questions need
different machinery. Relations come first — cheap, no node locations needed —
to learn which ways belong to a named cycling network. Ways come second, with
locations assembled so each way has real geometry.

Ferries are included. They are part of the network on the Atlantic seaboard, and
a router that silently drops them produces a route that cannot be ridden.
"""

from __future__ import annotations

import hashlib
import logging
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from itertools import pairwise
from pathlib import Path

from sqlalchemy import delete, insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)
from ..models.network import NetworkWay
from ..models.source import RouteSource, SourceAttribution, SourceImport
from ..routing.validation import haversine_m
from .osm_tags import (
    Interpreted,
    interpret_bicycle_access,
    interpret_boolean,
    interpret_cycle_infrastructure,
    interpret_road_class,
    interpret_speed_limit,
    interpret_surface,
    is_ferry,
    is_routable,
    oneway_for_bicycles,
    preserved,
)

logger = logging.getLogger(__name__)

# Ways shorter than this are almost always artefacts of how a junction was
# drawn rather than rideable pieces of road. They are still imported — dropping
# them would break the topology a router walks — but they are not counted when
# reporting how much of the network was surveyed.
NEGLIGIBLE_LENGTH_M = 2.0

# How pyosmium caches node coordinates while the ways are read. `flex_mem` keeps
# them in RAM and grows as needed, which suits a country-sized extract. A larger
# region needs a disk-backed store — `sparse_file_array,<path>` — because the
# cache, not the file, is what runs a host out of memory.
DEFAULT_LOCATION_STORAGE = "flex_mem"

# The attributes whose presence or absence decides `attribute_completeness`.
# Deliberately the ones that change a routing decision, not every tag OSM has.
_COMPLETENESS_ATTRIBUTES = (
    "surface_status",
    "bicycle_access_status",
    "road_class_status",
    "speed_limit_status",
    "cycle_lane_status",
)


class OsmiumUnavailableError(RuntimeError):
    """Raised when the reader is asked to run without pyosmium installed.

    Kept explicit so the failure names the remedy rather than surfacing as an
    ImportError from somewhere deep in a worker (§25.18).
    """


@dataclass(frozen=True, slots=True)
class NetworkRef:
    """One named cycling network a way belongs to, from an OSM route relation."""

    relation_id: int
    network: str | None
    ref: str | None
    name: str | None

    def as_dict(self) -> dict:
        return {
            "relation_id": self.relation_id,
            "network": self.network,
            "ref": self.ref,
            "name": self.name,
        }


@dataclass(frozen=True, slots=True)
class WayRecord:
    """One routable way, interpreted but not yet stored.

    A plain value so the whole tag-to-row translation can be tested without a
    database and without a PBF file.
    """

    osm_way_id: int
    coordinates: tuple[tuple[float, float], ...]
    tags: dict[str, str]

    surface_tag_raw: str | None
    surface_family: SurfaceFamily
    surface_status: KnowledgeStatus
    smoothness_tag_raw: str | None
    tracktype_tag_raw: str | None

    bicycle_access: BicycleAccess
    bicycle_access_status: KnowledgeStatus
    bicycle_access_raw: str | None
    oneway_bicycle: bool | None
    oneway_status: KnowledgeStatus
    access_conditional_raw: str | None
    seasonal_raw: str | None

    cycle_lane: CycleLaneKind | None
    cycle_lane_status: KnowledgeStatus
    cycle_lane_raw: str | None
    road_class: RoadClass
    road_class_status: KnowledgeStatus

    speed_limit_kph: int | None
    speed_limit_status: KnowledgeStatus
    width_m: float | None
    lit: bool | None
    shoulder: bool | None

    is_bridge: bool | None
    is_tunnel: bool | None
    is_ferry: bool
    is_ford: bool | None
    has_steps: bool | None
    barrier_raw: str | None
    construction_raw: str | None

    network_refs: tuple[NetworkRef, ...] = ()

    @property
    def length_m(self) -> float:
        """Geodesic length along the way's own vertices.

        Computed here rather than read back from PostGIS so the value exists
        before the row is written and so a failed import still reports how much
        network it saw.
        """
        return sum(
            haversine_m(_Point(lat=lat_a, lon=lon_a), _Point(lat=lat_b, lon=lon_b))
            for (lon_a, lat_a), (lon_b, lat_b) in pairwise(self.coordinates)
        )

    @property
    def attribute_completeness(self) -> float:
        """Share of the routing-relevant attributes that were actually recorded.

        A measure of evidence, never of quality. A fully surveyed boreen scores
        1.0 and is still a boreen (§3.7).
        """
        known = sum(
            1 for name in _COMPLETENESS_ATTRIBUTES if getattr(self, name) is KnowledgeStatus.KNOWN
        )
        return known / len(_COMPLETENESS_ATTRIBUTES)

    def ewkt(self) -> str:
        points = ", ".join(f"{lon:.7f} {lat:.7f}" for lon, lat in self.coordinates)
        return f"SRID=4326;LINESTRING({points})"


@dataclass(frozen=True, slots=True)
class _Point:
    """Minimal lat/lon for the shared haversine helper."""

    lat: float
    lon: float


@dataclass
class ImportCounters:
    """What the pass actually saw, for the SourceImport row and the logs."""

    ways_seen: int = 0
    ways_written: int = 0
    ways_rejected: int = 0
    ways_without_geometry: int = 0
    relations_seen: int = 0
    surface_known: int = 0
    access_known: int = 0
    ferry_ways: int = 0
    length_m_total: float = 0.0
    length_m_surface_known: float = 0.0

    def observe(self, record: WayRecord) -> None:
        self.ways_written += 1
        length = record.length_m
        self.length_m_total += length
        if record.surface_status is KnowledgeStatus.KNOWN:
            self.surface_known += 1
            self.length_m_surface_known += length
        if record.bicycle_access_status is KnowledgeStatus.KNOWN:
            self.access_known += 1
        if record.is_ferry:
            self.ferry_ways += 1

    def as_log(self) -> list[dict]:
        """The transformation log written onto the import row (§6.7).

        Shares are reported by distance as well as by count, because a network
        can be 60% surveyed by way count and 20% by kilometre when the surveyed
        parts are all short urban links.
        """
        surveyed_share = (
            self.length_m_surface_known / self.length_m_total if self.length_m_total > 0 else None
        )
        return [
            {
                "stage": "read",
                "ways_seen": self.ways_seen,
                "relations_seen": self.relations_seen,
                "ways_without_geometry": self.ways_without_geometry,
            },
            {
                "stage": "interpret",
                "ways_written": self.ways_written,
                "ways_rejected_not_routable": self.ways_rejected,
                "ferry_ways": self.ferry_ways,
            },
            {
                "stage": "coverage",
                "network_length_km": round(self.length_m_total / 1000.0, 1),
                "surface_surveyed_share_by_distance": (
                    round(surveyed_share, 4) if surveyed_share is not None else None
                ),
                "surface_surveyed_ways": self.surface_known,
                "access_surveyed_ways": self.access_known,
                "note": (
                    "An unsurveyed share is a property of the source, not a "
                    "defect in the import. It is carried through as unknown."
                ),
            },
        ]


def _raw(interpreted: Interpreted) -> str | None:
    return interpreted.raw


def interpret_way(
    osm_way_id: int,
    tags: dict[str, str],
    coordinates: Iterable[tuple[float, float]],
    *,
    network_refs: tuple[NetworkRef, ...] = (),
) -> WayRecord | None:
    """Translate one OSM way into a record, or ``None`` if it is not routable.

    Every branch here is a decision about what the source said. Where the source
    said nothing, the corresponding status stays ``UNKNOWN``; there is no code
    path that supplies a value the tags did not carry.
    """
    if not is_routable(tags):
        return None

    points = tuple(coordinates)
    if len(points) < 2:
        return None

    surface, family = interpret_surface(tags)
    access = interpret_bicycle_access(tags)
    lane = interpret_cycle_infrastructure(tags)
    road_class = interpret_road_class(tags)
    speed = interpret_speed_limit(tags)
    oneway = oneway_for_bicycles(tags)

    bridge = interpret_boolean(tags, "bridge")
    tunnel = interpret_boolean(tags, "tunnel")
    ford = interpret_boolean(tags, "ford")
    lit = interpret_boolean(tags, "lit")

    # `shoulder` is tagged both as a boolean and as a side ("right", "both").
    # A side value is a positive statement that one exists, which is the only
    # thing Contour reads it for.
    shoulder_raw = tags.get("shoulder")
    shoulder: bool | None
    if shoulder_raw is None:
        shoulder = None
    elif shoulder_raw in {"no", "none"}:
        shoulder = False
    else:
        shoulder = True

    return WayRecord(
        osm_way_id=osm_way_id,
        coordinates=points,
        tags=preserved(tags),
        surface_tag_raw=surface.raw,
        surface_family=family,
        surface_status=surface.status,
        smoothness_tag_raw=tags.get("smoothness"),
        tracktype_tag_raw=tags.get("tracktype"),
        bicycle_access=(
            access.value if isinstance(access.value, BicycleAccess) else BicycleAccess.UNKNOWN
        ),
        bicycle_access_status=access.status,
        bicycle_access_raw=_raw(access),
        oneway_bicycle=oneway.value if isinstance(oneway.value, bool) else None,
        oneway_status=oneway.status,
        access_conditional_raw=tags.get("bicycle:conditional") or tags.get("access:conditional"),
        seasonal_raw=tags.get("seasonal") or tags.get("winter_service"),
        cycle_lane=lane.value if isinstance(lane.value, CycleLaneKind) else None,
        cycle_lane_status=lane.status,
        cycle_lane_raw=_raw(lane),
        road_class=(
            road_class.value if isinstance(road_class.value, RoadClass) else RoadClass.UNKNOWN
        ),
        road_class_status=road_class.status,
        speed_limit_kph=speed.value if isinstance(speed.value, int) else None,
        speed_limit_status=speed.status,
        width_m=_parse_width(tags.get("width")),
        lit=lit.value if isinstance(lit.value, bool) else None,
        shoulder=shoulder,
        is_bridge=bridge.value if isinstance(bridge.value, bool) else None,
        is_tunnel=tunnel.value if isinstance(tunnel.value, bool) else None,
        is_ferry=is_ferry(tags),
        is_ford=ford.value if isinstance(ford.value, bool) else None,
        has_steps=tags.get("highway") == "steps" or None,
        barrier_raw=tags.get("barrier"),
        construction_raw=tags.get("construction") or tags.get("highway:construction"),
        network_refs=network_refs,
    )


def _parse_width(value: str | None) -> float | None:
    """Metres, or ``None`` when the value is not a plain metre measurement.

    OSM widths appear as "3", "3 m", "2.5m" and occasionally in feet. A value
    Contour cannot read is left unset rather than guessed at; the verbatim tag
    is still in ``tags`` for anyone who wants it.
    """
    if not value:
        return None
    text = value.strip().lower().removesuffix("m").strip()
    try:
        width = float(text)
    except ValueError:
        return None
    # Widths above about 30 m are mis-tagged (they are usually the whole
    # right-of-way, or a units error). Recorded as unreadable rather than used.
    if width <= 0 or width > 30.0:
        return None
    return width


# ---------------------------------------------------------------------------
# Reading a PBF
# ---------------------------------------------------------------------------


def _require_osmium():
    try:
        import osmium
    except ImportError as exc:  # pragma: no cover - exercised by absence
        raise OsmiumUnavailableError(
            "pyosmium is not installed. Install the ingestion extra with "
            "`uv sync --extra ingestion` in apps/api."
        ) from exc
    return osmium


def read_route_relations(path: Path) -> dict[int, list[NetworkRef]]:
    """Map way id to the cycling route relations that contain it.

    A separate pass because relations need no node locations, so this is a fast
    scan. Only ``type=route`` with ``route=bicycle`` is collected: a way being
    in a bus route says nothing about cycling.
    """
    osmium = _require_osmium()
    memberships: dict[int, list[NetworkRef]] = {}

    for relation in osmium.FileProcessor(str(path), osmium.osm.RELATION):
        tags = dict(relation.tags)
        if tags.get("type") != "route" or tags.get("route") != "bicycle":
            continue
        ref = NetworkRef(
            relation_id=relation.id,
            network=tags.get("network"),
            ref=tags.get("ref"),
            name=tags.get("name"),
        )
        for member in relation.members:
            if member.type != "w":
                continue
            memberships.setdefault(member.ref, []).append(ref)

    return memberships


def read_ways(
    path: Path,
    *,
    memberships: dict[int, list[NetworkRef]] | None = None,
    counters: ImportCounters | None = None,
    location_storage: str = DEFAULT_LOCATION_STORAGE,
) -> Iterator[WayRecord]:
    """Yield every routable way in the extract, with geometry.

    Ways whose nodes are missing from the extract are counted and skipped rather
    than emitted with partial geometry — a line drawn through the nodes that
    happen to be present is a different road from the one the surveyor mapped.
    """
    osmium = _require_osmium()
    memberships = memberships or {}
    counters = counters or ImportCounters()

    # Nodes must be *read* for their locations to be cached, so the entity mask
    # includes them and a filter drops them again before they reach this loop.
    # Restricting the mask to WAY alone fails outright — "Nodes not read from
    # file. Cannot enable location cache." — because a way in a PBF carries node
    # references, not coordinates.
    processor = (
        osmium.FileProcessor(str(path), osmium.osm.NODE | osmium.osm.WAY)
        .with_locations(location_storage)
        .with_filter(osmium.filter.EntityFilter(osmium.osm.WAY))
    )

    for way in processor:
        tags = dict(way.tags)
        counters.ways_seen += 1

        try:
            coordinates = tuple(
                (node.location.lon, node.location.lat)
                for node in way.nodes
                if node.location.valid()
            )
        except osmium.InvalidLocationError:
            counters.ways_without_geometry += 1
            continue

        if len(coordinates) != len(way.nodes):
            counters.ways_without_geometry += 1
            continue

        record = interpret_way(
            way.id,
            tags,
            coordinates,
            network_refs=tuple(memberships.get(way.id, ())),
        )
        if record is None:
            counters.ways_rejected += 1
            continue

        counters.observe(record)
        yield record


# ---------------------------------------------------------------------------
# Writing to PostGIS
# ---------------------------------------------------------------------------


def _row(record: WayRecord, *, source_id: uuid.UUID, import_id: uuid.UUID, now: datetime) -> dict:
    return {
        "id": uuid.uuid4(),
        "osm_way_id": record.osm_way_id,
        "source_id": source_id,
        "import_id": import_id,
        "geom": record.ewkt(),
        "length_m": record.length_m,
        "tags": record.tags,
        "surface_tag_raw": record.surface_tag_raw,
        "surface_family": record.surface_family,
        "surface_status": record.surface_status,
        "smoothness_tag_raw": record.smoothness_tag_raw,
        "tracktype_tag_raw": record.tracktype_tag_raw,
        "bicycle_access": record.bicycle_access,
        "bicycle_access_status": record.bicycle_access_status,
        "bicycle_access_raw": record.bicycle_access_raw,
        "oneway_bicycle": record.oneway_bicycle,
        "oneway_status": record.oneway_status,
        "access_conditional_raw": record.access_conditional_raw,
        "seasonal_raw": record.seasonal_raw,
        "cycle_lane": record.cycle_lane,
        "cycle_lane_status": record.cycle_lane_status,
        "cycle_lane_raw": record.cycle_lane_raw,
        "road_class": record.road_class,
        "road_class_status": record.road_class_status,
        "speed_limit_kph": record.speed_limit_kph,
        "speed_limit_status": record.speed_limit_status,
        "width_m": record.width_m,
        "lit": record.lit,
        "shoulder": record.shoulder,
        "is_bridge": record.is_bridge,
        "is_tunnel": record.is_tunnel,
        "is_ferry": record.is_ferry,
        "is_ford": record.is_ford,
        "has_steps": record.has_steps,
        "barrier_raw": record.barrier_raw,
        "construction_raw": record.construction_raw,
        "network_refs": [ref.as_dict() for ref in record.network_refs],
        "last_import_at": now,
        "attribute_completeness": record.attribute_completeness,
        "created_at": now,
        "updated_at": now,
    }


def _batched(records: Iterable[WayRecord], size: int) -> Iterator[list[WayRecord]]:
    batch: list[WayRecord] = []
    for record in records:
        batch.append(record)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def file_checksum(path: Path, *, chunk_size: int = 1 << 20) -> str:
    """SHA-256 of the extract, recorded so an import can be reproduced exactly."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class OsmNetworkImporter:
    """Loads a PBF extract into ``network_way`` with full provenance.

    Replacement is wholesale per source: a new import writes new rows and the
    previous import's rows are removed once it succeeds. Merging two vintages of
    OSM would produce a network that never existed at any point in time, and
    there would be no way to say which day a given lane's surface came from.
    """

    session: AsyncSession
    batch_size: int = 2000
    location_storage: str = DEFAULT_LOCATION_STORAGE
    counters: ImportCounters = field(default_factory=ImportCounters)

    async def run(
        self,
        path: Path,
        *,
        source_slug: str = "openstreetmap",
        idempotency_key: str | None = None,
        replace_existing: bool = True,
    ) -> SourceImport:
        source = await self._source(source_slug)
        checksum = file_checksum(path)
        key = idempotency_key or f"osm:{checksum}"

        existing = await self.session.scalar(
            select(SourceImport).where(SourceImport.idempotency_key == key)
        )
        if existing is not None and existing.status == "succeeded":
            logger.info("import %s already completed for %s", existing.id, path)
            return existing

        started = datetime.now(UTC)
        record = SourceImport(
            source_id=source.id,
            idempotency_key=key,
            started_at=started,
            status="running",
            stage="reading relations",
            stages_total=4,
            request_url=str(path),
            response_checksum=checksum,
            # The licence and attribution as they stand right now. Snapshotting
            # them means a later licence change cannot retroactively alter what
            # this import was permitted to do (§6.7).
            licence_snapshot_id=source.licence_id,
            attribution_snapshot=await self._attribution(source),
        )
        self.session.add(record)
        await self.session.flush()

        try:
            memberships = read_route_relations(path)
            self.counters.relations_seen = len(
                {r.relation_id for refs in memberships.values() for r in refs}
            )
            record.stage = "reading ways"
            record.stages_completed = 1
            await self.session.flush()

            previous_import_ids = (
                await self.session.scalars(
                    select(SourceImport.id).where(
                        SourceImport.source_id == source.id,
                        SourceImport.id != record.id,
                        SourceImport.status == "succeeded",
                    )
                )
            ).all()

            now = datetime.now(UTC)
            stream = read_ways(
                path,
                memberships=memberships,
                counters=self.counters,
                location_storage=self.location_storage,
            )
            for batch in _batched(stream, self.batch_size):
                await self.session.execute(
                    insert(NetworkWay),
                    [_row(way, source_id=source.id, import_id=record.id, now=now) for way in batch],
                )
                if self.counters.ways_written % 50_000 < self.batch_size:
                    logger.info(
                        "imported %s ways (%s km)",
                        self.counters.ways_written,
                        round(self.counters.length_m_total / 1000),
                    )

            record.stage = "replacing previous import"
            record.stages_completed = 2
            await self.session.flush()

            if replace_existing and previous_import_ids:
                await self.session.execute(
                    delete(NetworkWay).where(NetworkWay.import_id.in_(previous_import_ids))
                )

            record.stage = "complete"
            record.stages_completed = 4
            record.status = "succeeded"
            record.finished_at = datetime.now(UTC)
            record.records_seen = self.counters.ways_seen
            record.records_written = self.counters.ways_written
            record.records_rejected = self.counters.ways_rejected
            record.transformation_log = self.counters.as_log()
            await self.session.commit()
            return record
        except Exception as exc:
            await self.session.rollback()
            # Re-attach a failure row: the rollback discarded the running one,
            # and an import that vanishes on failure leaves no evidence of what
            # was attempted (§6.7).
            failure = SourceImport(
                source_id=source.id,
                idempotency_key=f"{key}:failed:{uuid.uuid4().hex[:8]}",
                started_at=started,
                finished_at=datetime.now(UTC),
                status="failed",
                stage="reading ways",
                request_url=str(path),
                response_checksum=checksum,
                records_seen=self.counters.ways_seen,
                records_written=0,
                error_code=type(exc).__name__,
                error_detail=str(exc)[:2000],
                transformation_log=self.counters.as_log(),
            )
            self.session.add(failure)
            await self.session.commit()
            raise

    async def _attribution(self, source: RouteSource) -> str | None:
        texts = (
            await self.session.scalars(
                select(SourceAttribution.text).where(SourceAttribution.source_id == source.id)
            )
        ).all()
        return "; ".join(texts) if texts else None

    async def _source(self, slug: str) -> RouteSource:
        source = await self.session.scalar(select(RouteSource).where(RouteSource.slug == slug))
        if source is None:
            raise LookupError(
                f"source '{slug}' is not registered. Run `python -m contour_api.seeds.sources` "
                "to seed the source registry before importing."
            )
        return source
