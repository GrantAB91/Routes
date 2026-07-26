"""Import, export and user report models."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamped, UUIDPrimaryKey


class ImportJob(Base, UUIDPrimaryKey, Timestamped):
    """A user-initiated file import (§15.1-15.5).

    The uploaded file is preserved unchanged (§15.2) and every derived geometry
    is stored beside it rather than replacing it, so the user can keep either
    version after seeing the difference (§15.5.9).
    """

    __tablename__ = "import_job"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_import_job_idempotency_key"),
        Index("ix_import_job_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    original_filename: Mapped[str] = mapped_column(String(500), nullable=False)
    # Detected by inspecting content, not by trusting the extension or the
    # client-supplied content type (§15.3, §20.5).
    detected_format: Mapped[str | None] = mapped_column(String(20))
    declared_content_type: Mapped[str | None] = mapped_column(String(120))
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    checksum_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    # Location of the untouched original.
    stored_uri: Mapped[str | None] = mapped_column(Text)

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    stage: Mapped[str | None] = mapped_column(String(80))
    stages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stages_total: Mapped[int | None] = mapped_column(Integer)

    route_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route.id", ondelete="SET NULL")
    )
    map_matched: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    match_deviation_max_m: Mapped[float | None] = mapped_column()
    # Which geometry the user chose to keep: "original" or "matched".
    kept_geometry: Mapped[str | None] = mapped_column(String(20))

    validation_report: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Structured, user-facing reason for rejection. Never a generic failure
    # message (§16.7).
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ExportJob(Base, UUIDPrimaryKey, Timestamped):
    """A route or stage export (§15.6).

    Every export carries a source and attribution manifest (§15.6.9), and an
    export is refused outright where a contributing licence forbids
    redistribution, with the restriction quoted (§20.9).
    """

    __tablename__ = "export_job"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_export_job_idempotency_key"),
        Index("ix_export_job_user", "user_id"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="CASCADE")
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)

    route_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_version.id", ondelete="SET NULL")
    )
    stage_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("stage.id", ondelete="SET NULL")
    )
    # "gpx" | "tcx" | "fit" | "kml" | "geojson" | "cue_sheet" | "summary_pdf" |
    # "elevation_csv"
    format: Mapped[str] = mapped_column(String(20), nullable=False)
    # "full_route" | "stage" | "selection" | "waypoints" | "pois"
    scope: Mapped[str] = mapped_column(String(30), nullable=False, default="full_route")
    selection_start_m: Mapped[float | None] = mapped_column()
    selection_end_m: Mapped[float | None] = mapped_column()

    status: Mapped[str] = mapped_column(String(30), nullable=False, default="pending")
    stage_name: Mapped[str | None] = mapped_column(String(80))
    stages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stages_total: Mapped[int | None] = mapped_column(Integer)

    output_uri: Mapped[str | None] = mapped_column(Text)
    output_checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    attribution_manifest: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    blocked_by_licence: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    blocked_reason: Mapped[str | None] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class UserReport(Base, UUIDPrimaryKey, Timestamped):
    """A problem a user reported about a route or a segment (§14.3.10).

    Reports are evidence about data quality, not corrections to it: a report
    never mutates a source-derived attribute. It is recorded, surfaced to
    operators, and can raise a :class:`SourceDiscrepancy` for review.
    """

    __tablename__ = "user_report"
    __table_args__ = (Index("ix_user_report_route", "route_id"),)

    reporter_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("app_user.id", ondelete="SET NULL")
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route.id", ondelete="CASCADE")
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_segment.id", ondelete="SET NULL")
    )
    # "wrong_surface" | "no_access" | "blocked" | "wrong_geometry" |
    # "licence_concern" | "other"
    category: Mapped[str] = mapped_column(String(60), nullable=False)
    detail: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="open")
    operator_note: Mapped[str | None] = mapped_column(Text)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
