"""The attributed cycling network imported from OpenStreetMap.

Distinct from :class:`~contour_api.models.route.RouteSegment`, which is a piece
of one route. This is the network itself: every routable way in the imported
region, with what its source actually said about it.

The separation matters because a route is transient and the network is not. Ten
routes crossing the same lane must agree about that lane's surface, and they do
because they all read this table rather than each carrying their own copy. It is
also what makes the attributor possible: Valhalla returns OSM way identifiers
with its geometry, and those join straight to these rows.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from geoalchemy2 import Geometry
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UUIDPrimaryKey
from .enums import (
    BicycleAccess,
    CycleLaneKind,
    KnowledgeStatus,
    RoadClass,
    SurfaceFamily,
)


class NetworkWay(Base, UUIDPrimaryKey, Timestamped):
    """One routable OSM way with its interpreted and verbatim attributes.

    Every interpreted attribute is paired with a :class:`KnowledgeStatus`, and
    ``tags`` keeps the source values verbatim so a segment can always be
    explained from what the surveyor wrote rather than from what Contour made of
    it (§6.3).
    """

    __tablename__ = "network_way"
    __table_args__ = (
        UniqueConstraint("osm_way_id", "import_id", name="uq_network_way_import"),
        Index("ix_network_way_osm_id", "osm_way_id"),
        Index("ix_network_way_road_class", "road_class"),
        # Postgres does not index a foreign key automatically, and replacing an
        # import deletes several hundred thousand rows by import_id.
        Index("ix_network_way_import", "import_id"),
    )

    osm_way_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="SET NULL")
    )
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_import.id", ondelete="CASCADE")
    )

    geom = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=True), nullable=False)
    length_m: Mapped[float | None] = mapped_column(Float)

    # Source tags, verbatim. This is what gets shown to a person; everything
    # below is Contour's reading of it.
    tags: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # -- Surface ------------------------------------------------------------
    surface_tag_raw: Mapped[str | None] = mapped_column(String(80))
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
    bicycle_access_raw: Mapped[str | None] = mapped_column(String(120))
    oneway_bicycle: Mapped[bool | None] = mapped_column(Boolean)
    oneway_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    access_conditional_raw: Mapped[str | None] = mapped_column(String(255))
    seasonal_raw: Mapped[str | None] = mapped_column(String(120))

    # -- Infrastructure and class -------------------------------------------
    cycle_lane: Mapped[CycleLaneKind | None] = mapped_column(
        Enum(CycleLaneKind, name="cycle_lane_kind")
    )
    cycle_lane_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )
    cycle_lane_raw: Mapped[str | None] = mapped_column(String(120))
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
    is_ferry: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_ford: Mapped[bool | None] = mapped_column(Boolean)
    has_steps: Mapped[bool | None] = mapped_column(Boolean)
    barrier_raw: Mapped[str | None] = mapped_column(String(80))
    construction_raw: Mapped[str | None] = mapped_column(String(80))

    # -- Route membership ---------------------------------------------------
    # Populated from OSM route relations; a way may belong to several networks.
    network_refs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # -- Provenance ---------------------------------------------------------
    source_date: Mapped[date | None] = mapped_column(Date)
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # 0..1 measure of how well evidenced this way is: what share of the
    # attributes Contour cares about were actually recorded. Never a measure of
    # how good or safe the road is.
    attribute_completeness: Mapped[float | None] = mapped_column(Float)
