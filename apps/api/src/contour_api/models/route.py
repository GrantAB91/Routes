"""Route, version, variant, segment and waypoint models.

Route versions are immutable (§18.4): editing a route writes a new
:class:`RouteVersion` rather than mutating the current one, which is what makes
undo, branching, comparison and restore possible without a separate history
mechanism.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, UUIDPrimaryKey
from .enums import (
    BicycleAccess,
    BicycleType,
    CycleLaneKind,
    GeneralisedSurface,
    KnowledgeStatus,
    RoadClass,
    RouteOriginKind,
    RouteVisibility,
    SurfaceFamily,
)

if TYPE_CHECKING:
    from .analysis import Climb, ElevationSample, RouteScore, ValidationResult
    from .journey import JourneyProject, Stage
    from .source import RouteSourceVersion


class Route(Base, UUIDPrimaryKey, Timestamped):
    """A catalogue entry.

    One route may be described by several sources; those descriptions live in
    :class:`~contour_api.models.source.RouteSourceVersion` rows and are grouped
    here by duplicate detection (§4.5) without any of them being discarded.
    """

    __tablename__ = "route"
    __table_args__ = (
        Index("ix_route_owner_visibility", "owner_user_id", "visibility"),
        Index("ix_route_origin_kind", "origin_kind"),
    )

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)

    origin_kind: Mapped[RouteOriginKind] = mapped_column(
        Enum(RouteOriginKind, name="route_origin_kind"), nullable=False
    )
    visibility: Mapped[RouteVisibility] = mapped_column(
        Enum(RouteVisibility, name="route_visibility"),
        nullable=False,
        default=RouteVisibility.PRIVATE,
    )

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE")
    )
    organisation_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("organisation.id", ondelete="SET NULL")
    )
    journey_project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journey_project.id", ondelete="SET NULL")
    )

    current_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("route_version.id", ondelete="SET NULL", use_alter=True),
    )

    country_codes: Mapped[list[str]] = mapped_column(
        ARRAY(String(2)), nullable=False, default=list
    )
    region_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), nullable=False, default=list
    )
    network_names: Mapped[list[str]] = mapped_column(
        ARRAY(String(120)), nullable=False, default=list
    )

    # Set when any contributing source forbids redistribution. Publish and
    # export consult this before acting and explain the exact restriction
    # rather than silently dropping the affected sections (§14.5, §20.9).
    redistribution_blocked: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    redistribution_block_reason: Mapped[str | None] = mapped_column(Text)

    versions: Mapped[list[RouteVersion]] = relationship(
        back_populates="route",
        foreign_keys="RouteVersion.route_id",
        cascade="all, delete-orphan",
    )
    source_versions: Mapped[list[RouteSourceVersion]] = relationship(back_populates="route")
    journey_project: Mapped[JourneyProject | None] = relationship(back_populates="routes")


class RouteVersion(Base, UUIDPrimaryKey, Timestamped):
    """An immutable snapshot of a route's geometry and metrics.

    Every edit creates a new version whose ``parent_version_id`` points at what
    it was derived from, giving undo/redo, named versions, branching and
    comparison a single shared mechanism (§9.1.20-23, §18.4).
    """

    __tablename__ = "route_version"
    __table_args__ = (
        UniqueConstraint("route_id", "version_number", name="uq_route_version_number"),
        Index("ix_route_version_route", "route_id"),
        CheckConstraint("version_number > 0", name="version_number_positive"),
    )

    route_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route.id", ondelete="CASCADE"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="SET NULL")
    )
    # User-supplied name for a version worth returning to (§9.1.20).
    label: Mapped[str | None] = mapped_column(String(255))
    # What produced this version: "generated" | "manual_edit" | "import" |
    # "partial_reroute" | "stage_split" | "restore".
    change_kind: Mapped[str] = mapped_column(String(40), nullable=False)
    change_summary: Mapped[str | None] = mapped_column(Text)
    created_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )

    # Geometries are kept apart so a matched line never overwrites what a source
    # or a user actually drew (§6.8, §18.3).
    geom_generated = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=True))
    geom_original = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=True))
    geom_matched = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=True))

    # Metrics. Every "unknown" counterpart is stored explicitly rather than
    # inferred from a zero (§18.6).
    distance_m: Mapped[float | None] = mapped_column(Float)
    ascent_m: Mapped[float | None] = mapped_column(Float)
    descent_m: Mapped[float | None] = mapped_column(Float)
    min_elevation_m: Mapped[float | None] = mapped_column(Float)
    max_elevation_m: Mapped[float | None] = mapped_column(Float)
    net_elevation_change_m: Mapped[float | None] = mapped_column(Float)
    elevation_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    # Share of route length with no elevation coverage, reported alongside the
    # ascent figure so the number is never read as more certain than it is.
    elevation_gap_m: Mapped[float | None] = mapped_column(Float)

    max_grade_percent: Mapped[float | None] = mapped_column(Float)
    # Keyed by analysis window in metres ("25", "100", "500", "1000") because a
    # gradient figure is meaningless without the window it was measured over
    # (§11.6). Populated only for windows the elevation source can support.
    sustained_grade_percent: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Distance in metres per surface family, per cycle infrastructure kind and
    # per road class. "unknown" is a key in each, never omitted (§7.9.8).
    surface_composition_m: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cycle_infrastructure_composition_m: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    road_class_composition_m: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    ferry_distance_m: Mapped[float | None] = mapped_column(Float)
    ferry_crossing_count: Mapped[int | None] = mapped_column(Integer)

    bicycle_type: Mapped[BicycleType | None] = mapped_column(
        Enum(BicycleType, name="bicycle_type")
    )
    # Engine and data provenance for this exact geometry, so a result stays
    # explainable after the engine or its tiles are upgraded.
    routing_provider: Mapped[str | None] = mapped_column(String(60))
    routing_engine_version: Mapped[str | None] = mapped_column(String(60))
    routing_tile_version: Mapped[str | None] = mapped_column(String(120))
    elevation_provider: Mapped[str | None] = mapped_column(String(60))
    elevation_dataset_id: Mapped[str | None] = mapped_column(String(200))

    route: Mapped[Route] = relationship(
        back_populates="versions", foreign_keys=[route_id]
    )
    segments: Mapped[list[RouteSegment]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )
    waypoints: Mapped[list[Waypoint]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )
    climbs: Mapped[list[Climb]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )
    elevation_samples: Mapped[list[ElevationSample]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )
    scores: Mapped[list[RouteScore]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )
    validation_results: Mapped[list[ValidationResult]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )
    stages: Mapped[list[Stage]] = relationship(
        back_populates="route_version", cascade="all, delete-orphan"
    )


class RouteVariant(Base, UUIDPrimaryKey, Timestamped):
    """One alternative in a comparison set (§7.8-7.9).

    Alternatives must be meaningfully different, so the difference measures
    against the rest of the set are stored rather than recomputed on demand —
    they are what the comparison screen ranks and explains from.
    """

    __tablename__ = "route_variant"
    __table_args__ = (
        UniqueConstraint("comparison_key", "slug", name="uq_route_variant_slug"),
        Index("ix_route_variant_comparison", "comparison_key"),
    )

    comparison_key: Mapped[str] = mapped_column(String(120), nullable=False)
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    # Plain-language account of why this alternative was produced and what it
    # optimises, shown on the route card (§3.6).
    selection_reason: Mapped[str | None] = mapped_column(Text)

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )

    shared_distance_m: Mapped[float | None] = mapped_column(Float)
    unique_distance_m: Mapped[float | None] = mapped_column(Float)
    # Distance from the reference corridor, e.g. the official Wild Atlantic Way
    # line, when the request was made against one (§7.9.12, §13.8.8).
    corridor_max_deviation_m: Mapped[float | None] = mapped_column(Float)
    corridor_mean_deviation_m: Mapped[float | None] = mapped_column(Float)
    detour_ratio: Mapped[float | None] = mapped_column(Float)
    # Per-variant overlap, keyed by the other variant's slug.
    overlap_with: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class RouteSegment(Base, UUIDPrimaryKey):
    """One attributed piece of a route version.

    Hot attributes are denormalised onto this table because routing validation
    and composition metrics scan every segment of every candidate, and an
    attribute-per-row join at that volume is too slow. The full attribute set,
    including per-attribute provenance and the long tail of source tags, lives
    in :class:`SegmentAttribute`; this table is a derived index over it, not a
    replacement for it.

    Every denormalised attribute is paired with a status column. Reading a value
    without its status is a bug: ``surface_family`` is ``UNKNOWN`` far more often
    than a map suggests, and treating that as ``PAVED`` is exactly the failure
    §2.6 forbids.
    """

    __tablename__ = "route_segment"
    __table_args__ = (
        UniqueConstraint("route_version_id", "sequence", name="uq_route_segment_sequence"),
        Index("ix_route_segment_version", "route_version_id"),
        CheckConstraint("distance_m >= 0", name="distance_non_negative"),
    )

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    geom = mapped_column(
        Geometry("LINESTRING", srid=4326, spatial_index=True), nullable=False
    )

    start_distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)

    # Identity in the routing graph, so a segment can be excluded by name on a
    # re-solve rather than by geometry guesswork.
    graph_edge_id: Mapped[str | None] = mapped_column(String(80))
    osm_way_id: Mapped[int | None] = mapped_column(Integer)

    # -- Surface ------------------------------------------------------------
    # The source's own value, verbatim (e.g. "asphalt", "sett", "fine_gravel").
    surface_tag_raw: Mapped[str | None] = mapped_column(String(80))
    # The routing engine's lossy generalisation of it; see GeneralisedSurface.
    surface_generalised: Mapped[GeneralisedSurface | None] = mapped_column(
        Enum(GeneralisedSurface, name="generalised_surface")
    )
    surface_family: Mapped[SurfaceFamily] = mapped_column(
        Enum(SurfaceFamily, name="surface_family"),
        nullable=False,
        default=SurfaceFamily.UNKNOWN,
    )
    surface_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    smoothness_tag_raw: Mapped[str | None] = mapped_column(String(80))
    tracktype_tag_raw: Mapped[str | None] = mapped_column(String(40))

    # -- Access -------------------------------------------------------------
    bicycle_access: Mapped[BicycleAccess] = mapped_column(
        Enum(BicycleAccess, name="bicycle_access"),
        nullable=False,
        default=BicycleAccess.UNKNOWN,
    )
    bicycle_access_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    oneway_bicycle: Mapped[bool | None] = mapped_column(Boolean)
    access_conditional_raw: Mapped[str | None] = mapped_column(Text)
    seasonal_access_raw: Mapped[str | None] = mapped_column(Text)

    # -- Infrastructure and classification ----------------------------------
    cycle_lane: Mapped[CycleLaneKind | None] = mapped_column(
        Enum(CycleLaneKind, name="cycle_lane_kind")
    )
    cycle_lane_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    road_class: Mapped[RoadClass] = mapped_column(
        Enum(RoadClass, name="road_class"), nullable=False, default=RoadClass.UNKNOWN
    )
    road_class_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    speed_limit_kph: Mapped[int | None] = mapped_column(Integer)
    speed_limit_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    width_m: Mapped[float | None] = mapped_column(Float)
    lit: Mapped[bool | None] = mapped_column(Boolean)
    shoulder: Mapped[bool | None] = mapped_column(Boolean)

    # -- Structures ---------------------------------------------------------
    is_bridge: Mapped[bool | None] = mapped_column(Boolean)
    is_tunnel: Mapped[bool | None] = mapped_column(Boolean)
    is_ferry: Mapped[bool | None] = mapped_column(Boolean)
    is_ford: Mapped[bool | None] = mapped_column(Boolean)
    has_steps: Mapped[bool | None] = mapped_column(Boolean)
    barrier_raw: Mapped[str | None] = mapped_column(String(80))
    construction_status_raw: Mapped[str | None] = mapped_column(String(80))

    # -- Terrain ------------------------------------------------------------
    grade_percent: Mapped[float | None] = mapped_column(Float)
    ascent_m: Mapped[float | None] = mapped_column(Float)
    descent_m: Mapped[float | None] = mapped_column(Float)
    elevation_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )

    # -- Provenance ---------------------------------------------------------
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="SET NULL")
    )
    source_date: Mapped[date | None] = mapped_column(Date)
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 0..1, derived from source agreement, attribute completeness and data age.
    # The derivation is documented in docs/route_scoring.md; it is a measure of
    # how well-evidenced the segment is, never of how safe it is.
    confidence: Mapped[float | None] = mapped_column(Float)

    route_version: Mapped[RouteVersion] = relationship(back_populates="segments")
    attributes: Mapped[list[SegmentAttribute]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )
    memberships: Mapped[list[BicycleRouteMembership]] = relationship(
        back_populates="segment", cascade="all, delete-orphan"
    )


class SegmentAttribute(Base, UUIDPrimaryKey):
    """One attribute of one segment, with its own provenance.

    Kept as rows rather than columns so that a segment can carry any attribute a
    source publishes without a migration, and so that each attribute records
    which source said it and when. This is what the segment inspector reads for
    per-field source, date and confidence (§10.8.11-10.8.15).
    """

    __tablename__ = "segment_attribute"
    __table_args__ = (
        UniqueConstraint("segment_id", "key", "source_id", name="uq_segment_attribute_key"),
        Index("ix_segment_attribute_segment", "segment_id"),
        Index("ix_segment_attribute_key", "key"),
    )

    segment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_segment.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    # Verbatim source value. Null with status UNKNOWN means the source was
    # consulted and said nothing — which is itself a fact worth storing.
    value_raw: Mapped[str | None] = mapped_column(Text)
    # Contour's interpretation, when one exists and is distinct from the raw
    # value. Kept separate so an interpretation is never mistaken for a source.
    value_normalised: Mapped[str | None] = mapped_column(Text)
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="SET NULL")
    )
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source_version.id", ondelete="SET NULL")
    )
    source_date: Mapped[date | None] = mapped_column(Date)
    confidence: Mapped[float | None] = mapped_column(Float)

    segment: Mapped[RouteSegment] = relationship(back_populates="attributes")


class BicycleRouteMembership(Base, UUIDPrimaryKey):
    """Membership of a segment in a signed or designated bicycle route (§6.4.4-5).

    Membership is evidence that a route is signed, not evidence that it is
    legal, surfaced, or suitable for any particular bicycle. Nothing downstream
    derives access or surface from it.
    """

    __tablename__ = "bicycle_route_membership"
    __table_args__ = (
        Index("ix_bicycle_route_membership_segment", "segment_id"),
        Index("ix_bicycle_route_membership_ref", "network", "route_ref"),
    )

    segment_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_segment.id", ondelete="CASCADE"), nullable=False
    )
    # OSM network level: "icn" | "ncn" | "rcn" | "lcn", or a source's own term.
    network: Mapped[str | None] = mapped_column(String(40))
    route_ref: Mapped[str | None] = mapped_column(String(120))
    route_name: Mapped[str | None] = mapped_column(String(500))
    operator: Mapped[str | None] = mapped_column(String(255))
    relation_id: Mapped[int | None] = mapped_column(Integer)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="SET NULL")
    )

    segment: Mapped[RouteSegment] = relationship(back_populates="memberships")


class Waypoint(Base, UUIDPrimaryKey, Timestamped):
    """A point the user placed on a route.

    ``locked`` marks a waypoint the user has fixed: rebalancing, rerouting and
    alternative generation must honour it rather than optimise it away
    (§9.1.7-8, §12.7).
    """

    __tablename__ = "waypoint"
    __table_args__ = (
        UniqueConstraint("route_version_id", "sequence", name="uq_waypoint_sequence"),
        Index("ix_waypoint_version", "route_version_id"),
    )

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)
    # "start" | "via" | "finish" | "stage_end"
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="via")
    name: Mapped[str | None] = mapped_column(String(255))
    locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Where the user's click was snapped to, kept alongside the click itself so
    # a surprising snap can be shown and undone.
    snapped_geom = mapped_column(Geometry("POINT", srid=4326))
    snap_distance_m: Mapped[float | None] = mapped_column(Float)

    route_version: Mapped[RouteVersion] = relationship(back_populates="waypoints")


class AvoidArea(Base, UUIDPrimaryKey, Timestamped):
    """A polygon the user drew to keep routes out of (§7.4.3, §9.1.11)."""

    __tablename__ = "avoid_area"
    __table_args__ = (Index("ix_avoid_area_owner", "owner_user_id"),)

    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE")
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route.id", ondelete="CASCADE")
    )
    name: Mapped[str | None] = mapped_column(String(255))
    geom = mapped_column(
        Geometry("POLYGON", srid=4326, spatial_index=True), nullable=False
    )
    # A hard avoid is a constraint; a soft one is a preference with a penalty.
    # Keeping them distinct is what lets the feasibility verdict distinguish
    # "impossible" from "expensive" (§7.3).
    hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    reason: Mapped[str | None] = mapped_column(Text)
