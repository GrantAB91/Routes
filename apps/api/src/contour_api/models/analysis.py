"""Elevation, climb, scoring and validation models.

Raw elevation samples and processed analysis are stored separately (§11.4) so
that a change to smoothing, the grade window set, or climb detection can be
re-run without re-querying the elevation source, and so that a user can compare
the raw and processed profiles (§11.13).
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    Boolean,
    CheckConstraint,
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
from .enums import FeasibilityVerdict, KnowledgeStatus

if TYPE_CHECKING:
    from .route import RouteVersion


class ElevationSample(Base, UUIDPrimaryKey):
    """One raw elevation reading along a route version.

    ``elevation_m`` is nullable: where the DEM has no coverage the sample is
    stored with a null value and ``status = UNKNOWN`` rather than being dropped
    or interpolated across. A gap that is silently bridged becomes invisible
    ascent, which is the most common way an elevation figure becomes a lie.
    """

    __tablename__ = "elevation_sample"
    __table_args__ = (
        UniqueConstraint("route_version_id", "sequence", name="uq_elevation_sample_sequence"),
        Index("ix_elevation_sample_version", "route_version_id"),
    )

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    geom = mapped_column(Geometry("POINT", srid=4326), nullable=False)

    elevation_m: Mapped[float | None] = mapped_column(Float)
    status: Mapped[KnowledgeStatus] = mapped_column(
        Enum(KnowledgeStatus, name="knowledge_status"),
        nullable=False,
        default=KnowledgeStatus.UNKNOWN,
    )

    provider: Mapped[str | None] = mapped_column(String(60))
    dataset_id: Mapped[str | None] = mapped_column(String(200))
    # Native horizontal resolution of the source at this location, in metres.
    # Sampling finer than this would present false precision (§11.3), so the
    # sampling interval is chosen from it rather than from a fixed constant.
    source_resolution_m: Mapped[float | None] = mapped_column(Float)

    route_version: Mapped[RouteVersion] = relationship(back_populates="elevation_samples")


class Climb(Base, UUIDPrimaryKey, Timestamped):
    """A detected climb (§11.7).

    Detection parameters are stored on the row because they are not universal:
    a climb found with one minimum-gain threshold is not the same object as one
    found with another, and comparing routes analysed under different parameters
    would be meaningless.
    """

    __tablename__ = "climb"
    __table_args__ = (
        Index("ix_climb_version", "route_version_id"),
        CheckConstraint("end_distance_m > start_distance_m", name="climb_length_positive"),
    )

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stage.id", ondelete="SET NULL")
    )
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str | None] = mapped_column(String(255))

    start_distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    end_distance_m: Mapped[float] = mapped_column(Float, nullable=False)
    length_m: Mapped[float] = mapped_column(Float, nullable=False)
    elevation_gain_m: Mapped[float] = mapped_column(Float, nullable=False)
    start_elevation_m: Mapped[float | None] = mapped_column(Float)
    end_elevation_m: Mapped[float | None] = mapped_column(Float)
    start_geom = mapped_column(Geometry("POINT", srid=4326))
    end_geom = mapped_column(Geometry("POINT", srid=4326))
    geom = mapped_column(Geometry("LINESTRING", srid=4326, spatial_index=True))

    average_grade_percent: Mapped[float] = mapped_column(Float, nullable=False)
    max_grade_percent: Mapped[float | None] = mapped_column(Float)
    # Keyed by window in metres, e.g. {"25": 14.2, "100": 11.8, "500": 8.1}.
    max_sustained_grade_percent: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Distance in metres per gradient bucket, e.g. {"0-3": 400, "3-6": 1200}.
    gradient_distribution_m: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    surface_composition_m: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    cycle_infrastructure_composition_m: Mapped[dict] = mapped_column(
        JSONB, nullable=False, default=dict
    )
    road_class_composition_m: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Share of the climb with no elevation coverage. A climb detected across a
    # data gap is reported with the gap stated, not presented as measured.
    elevation_gap_m: Mapped[float | None] = mapped_column(Float)
    confidence: Mapped[float | None] = mapped_column(Float)
    detection_parameters: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    route_version: Mapped[RouteVersion] = relationship(back_populates="climbs")


class RouteScore(Base, UUIDPrimaryKey, Timestamped):
    """Scoring of one route version, stored by component (§18.5).

    The total alone cannot explain a ranking. Components are kept so the
    comparison screen can show *why* one alternative ranked above another, and
    so a weighting change can be re-applied without re-routing.
    """

    __tablename__ = "route_score"
    __table_args__ = (
        UniqueConstraint("route_version_id", "profile_key", name="uq_route_score_profile"),
    )

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    # Which preference weighting produced this score, e.g. "lowest_ascent".
    profile_key: Mapped[str] = mapped_column(String(120), nullable=False)
    total: Mapped[float | None] = mapped_column(Float)
    # {component_key: {"raw": float, "weight": float, "weighted": float,
    #                  "status": "known"|"unknown"}}
    components: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Components that could not be evaluated because their input data was
    # missing. Surfaced next to the score so a high score is not mistaken for a
    # thoroughly evidenced one.
    unscored_components: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    method_version: Mapped[str | None] = mapped_column(String(40))

    route_version: Mapped[RouteVersion] = relationship(back_populates="scores")


class ValidationResult(Base, UUIDPrimaryKey, Timestamped):
    """Outcome of validating a route version against §7.10 and its constraints.

    A validation result is never a boolean. ``verdict`` distinguishes a route
    proven compliant from one that merely could not be disproved because data
    was missing, and ``violations`` names the exact segments at fault so the
    user can see and act on them (§7.12).
    """

    __tablename__ = "validation_result"
    __table_args__ = (Index("ix_validation_result_version", "route_version_id"),)

    route_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="CASCADE"), nullable=False
    )
    intent_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_intent.id", ondelete="SET NULL")
    )
    verdict: Mapped[FeasibilityVerdict] = mapped_column(
        Enum(FeasibilityVerdict, name="feasibility_verdict"), nullable=False
    )

    # One entry per §7.10 check: {"check": "connected_geometry", "passed": true,
    # "status": "known"|"unknown", "detail": "..."}.
    checks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # [{"constraint_key": "max_gradient_percent", "requested": 12.0,
    #   "observed": 15.4, "segment_ids": [...], "distance_m": 240.0}]
    violations: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # Checks that could not be performed at all, and why.
    unevaluated: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    warnings: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # How many solve -> validate -> re-solve passes were spent, and whether the
    # loop gave up with a non-compliant best effort (docs/route_validation.md).
    resolve_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    exhausted_attempts: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    summary: Mapped[str | None] = mapped_column(Text)

    route_version: Mapped[RouteVersion] = relationship(back_populates="validation_results")
