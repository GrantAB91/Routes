"""Route intent and constraint models.

A :class:`RouteIntent` is the structured, versioned form of what the user asked
for (§8.2). It is produced by a parser, shown to the user before any routing
happens, and is fully editable (§8.3). Routing reads only the intent, never the
original free text — which is what keeps a language model out of the geometry
path entirely (§8.4, §8.6).
"""

from __future__ import annotations

import uuid
from datetime import date

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    Date,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, UUIDPrimaryKey
from .enums import BicycleType


class RouteIntent(Base, UUIDPrimaryKey, Timestamped):
    """A parsed, user-confirmed route request.

    ``schema_version`` is stored because intents are persisted with saved
    routes: a route generated last month must remain explainable under the
    intent schema that produced it.
    """

    __tablename__ = "route_intent"
    __table_args__ = (Index("ix_route_intent_owner", "owner_user_id"),)

    schema_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE")
    )
    journey_project_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journey_project.id", ondelete="SET NULL")
    )

    # What the user typed, kept verbatim so a mis-parse can be diagnosed.
    request_text: Mapped[str | None] = mapped_column(Text)
    parser: Mapped[str] = mapped_column(String(40), nullable=False, default="deterministic")
    parser_version: Mapped[str | None] = mapped_column(String(40))
    # Per-field record of how each value was arrived at: which phrase produced
    # it, or that the user set it by hand. Drives the "here is what I understood"
    # screen and makes an incorrect parse correctable rather than mysterious.
    parse_trace: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Fields the parser could not determine. Only those that make execution
    # impossible are asked about (§8.5.2); the rest stay unset and unused.
    unresolved_fields: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    confirmed_by_user: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # 1-4. Endpoints and shape
    origin_text: Mapped[str | None] = mapped_column(String(500))
    origin_geom = mapped_column(Geometry("POINT", srid=4326))
    destination_text: Mapped[str | None] = mapped_column(String(500))
    destination_geom = mapped_column(Geometry("POINT", srid=4326))
    via_points = mapped_column(Geometry("MULTIPOINT", srid=4326))
    via_texts: Mapped[list[str]] = mapped_column(ARRAY(String(500)), nullable=False, default=list)
    # "forward" | "reverse" | "either" — the Wild Atlantic Way is planned in
    # both directions, and direction changes the route, not just its ordering.
    direction: Mapped[str | None] = mapped_column(String(20))

    # 5-6. Bicycle and surface
    bicycle_type: Mapped[BicycleType | None] = mapped_column(Enum(BicycleType, name="bicycle_type"))
    surface_requirements: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # 7-13. Distance, time and gradient limits
    max_total_distance_m: Mapped[float | None] = mapped_column(Float)
    max_daily_distance_m: Mapped[float | None] = mapped_column(Float)
    max_daily_riding_time_s: Mapped[int | None] = mapped_column(Integer)
    max_daily_ascent_m: Mapped[float | None] = mapped_column(Float)
    max_gradient_percent: Mapped[float | None] = mapped_column(Float)
    preferred_gradient_percent: Mapped[float | None] = mapped_column(Float)
    number_of_days: Mapped[int | None] = mapped_column(Integer)

    # 14-19. Stops, places and avoidances
    fixed_overnight_stops: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    flexible_overnight_areas: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    required_pois: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    avoided_roads: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    avoided_areas: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    ferry_preference: Mapped[str | None] = mapped_column(String(30))

    # 20-24. Preferences and output
    scenic_preference: Mapped[str | None] = mapped_column(String(30))
    coast_proximity_m: Mapped[float | None] = mapped_column(Float)
    cycle_infrastructure_preference: Mapped[str | None] = mapped_column(String(30))
    # How much route distance with unknown attributes the user will accept.
    # Null means "not stated", which is treated as a reporting requirement, not
    # as unlimited tolerance.
    unknown_data_tolerance_ratio: Mapped[float | None] = mapped_column(Float)
    export_target: Mapped[str | None] = mapped_column(String(40))

    start_date: Mapped[date | None] = mapped_column(Date)

    constraints: Mapped[list[RouteConstraint]] = relationship(
        back_populates="intent", cascade="all, delete-orphan"
    )


class RouteConstraint(Base, UUIDPrimaryKey, Timestamped):
    """One constraint derived from an intent.

    ``hard`` is the distinction that drives everything downstream (§7.3): a hard
    constraint that cannot be met makes a route ``NOT_FEASIBLE``, while an unmet
    preference only lowers a score. Constraints are stored individually rather
    than as a blob so a violation can point back at the exact requirement the
    user set, in their words.
    """

    __tablename__ = "route_constraint"
    __table_args__ = (Index("ix_route_constraint_intent", "intent_id"),)

    intent_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_intent.id", ondelete="CASCADE"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    hard: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # Comparator and value, e.g. {"op": "<=", "value": 12.0, "unit": "percent"}.
    predicate: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    weight: Mapped[float | None] = mapped_column(Float)
    # The user's own phrasing, echoed back when the constraint is violated so
    # the explanation uses their words rather than an internal key.
    stated_as: Mapped[str | None] = mapped_column(Text)

    intent: Mapped[RouteIntent] = relationship(back_populates="constraints")
