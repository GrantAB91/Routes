"""Journey project, stage and point-of-interest models."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
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
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, Timestamped, UUIDPrimaryKey
from .enums import KnowledgeStatus, StageBalanceStrategy

if TYPE_CHECKING:
    from .route import Route, RouteVersion


class JourneyProject(Base, UUIDPrimaryKey, Timestamped):
    """A route that has become a multi-day plan (§12.1)."""

    __tablename__ = "journey_project"
    __table_args__ = (Index("ix_journey_project_owner", "owner_user_id"),)

    name: Mapped[str] = mapped_column(String(500), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    owner_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE")
    )
    slug: Mapped[str] = mapped_column(String(160), nullable=False, unique=True)

    start_date: Mapped[date | None] = mapped_column(Date)
    # Journey-level constraints (§12.2). Held as a document rather than columns
    # because the set is user-extensible and is versioned with the plan.
    constraints: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # True when the stage plan came from a worked example rather than the user's
    # own inputs. Presets must be labelled as examples and never presented as a
    # personal recommendation (§13.13).
    is_example_plan: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    example_plan_note: Mapped[str | None] = mapped_column(Text)

    routes: Mapped[list[Route]] = relationship(back_populates="journey_project")
    stages: Mapped[list[Stage]] = relationship(
        back_populates="journey_project", cascade="all, delete-orphan"
    )


class Stage(Base, UUIDPrimaryKey, Timestamped):
    """One day, or one leg, of a journey.

    Stages are versioned alongside route versions (§12.9): a stage row belongs
    to the route version it was computed against, so a reroute of one stage
    never silently rewrites the metrics of the others (§12.8).
    """

    __tablename__ = "stage"
    __table_args__ = (
        UniqueConstraint("route_version_id", "sequence", name="uq_stage_sequence"),
        Index("ix_stage_project", "journey_project_id"),
    )

    journey_project_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("journey_project.id", ondelete="CASCADE"), nullable=False
    )
    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))
    planned_date: Mapped[date | None] = mapped_column(Date)
    is_rest_day: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    start_distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    end_distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    ascent_m: Mapped[float | None] = mapped_column(Float)
    descent_m: Mapped[float | None] = mapped_column(Float)
    max_grade_percent: Mapped[float | None] = mapped_column(Float)
    elevation_status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )

    start_geom = mapped_column(Geometry("POINT", srid=4326))
    end_geom = mapped_column(Geometry("POINT", srid=4326))
    # Endpoint the user fixed; rebalancing must not move it (§12.2.7, §12.7).
    end_locked: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # Why this overnight point was chosen, in plain language (§12.7).
    selection_reason: Mapped[str | None] = mapped_column(Text)
    balance_strategy: Mapped[StageBalanceStrategy | None] = mapped_column(
        Enum(StageBalanceStrategy, name="stage_balance_strategy")
    )
    constraint_violations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Services the stage endpoint needs but which no verified source confirms.
    # Shown as an explicit absence, never as "none nearby" (§12.4).
    unknown_services: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    journey_project: Mapped[JourneyProject] = relationship(back_populates="stages")
    route_version: Mapped[RouteVersion] = relationship(back_populates="stages")


class PointOfInterest(Base, UUIDPrimaryKey, Timestamped):
    """A place near a route, from a verified source only (§12.3).

    Contour does not infer that a service exists because a settlement is
    nearby, and does not carry opening hours or availability beyond what a
    source published and when. ``observed_at`` and ``source_id`` are therefore
    mandatory context for anything shown to a user (§12.10).
    """

    __tablename__ = "point_of_interest"
    __table_args__ = (
        Index("ix_poi_geom_category", "category"),
        UniqueConstraint("source_id", "source_record_id", name="uq_poi_source_record"),
    )

    name: Mapped[str | None] = mapped_column(String(500))
    # "water" | "food" | "accommodation" | "camping" | "bicycle_repair" |
    # "public_transport" | "ferry" | "toilets" | "shelter" | "medical" |
    # "ebike_charging" | "signature_discovery_point"
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    geom = mapped_column(Geometry("POINT", srid=4326, spatial_index=True), nullable=False)

    source_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="SET NULL")
    )
    source_record_id: Mapped[str | None] = mapped_column(String(255))
    source_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source_version.id", ondelete="SET NULL")
    )
    published_description: Mapped[str | None] = mapped_column(Text)
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # When the source last confirmed this, distinct from when Contour imported
    # it. A POI is presented with both dates so staleness is visible.
    observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_import_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # e.g. opening hours as published, verbatim. Never normalised into a
    # boolean "open now", which Contour cannot know.
    opening_hours_raw: Mapped[str | None] = mapped_column(Text)
