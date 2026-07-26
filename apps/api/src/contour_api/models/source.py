"""Source, licence, attribution, import and discrepancy models.

This module is the machine-readable half of ``docs/source_registry.md`` (§2.3,
§2.8). The document and these tables carry the same twenty fields; the seeding
script in ``contour_api.seeds.source_registry`` is the single writer of both, so
they cannot drift apart.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING

from geoalchemy2 import Geometry
from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
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
from .enums import ConnectorStatus, RedistributionPermission

if TYPE_CHECKING:
    from .route import Route


class SourceLicence(Base, UUIDPrimaryKey, Timestamped):
    """A licence as published, captured verbatim.

    ``full_text`` is stored rather than referenced because licence pages change.
    An export or publish decision made in the past must remain explainable
    against the licence that was actually in force at the time, which is what
    :attr:`SourceImport.licence_snapshot_id` pins.
    """

    __tablename__ = "source_licence"

    identifier: Mapped[str] = mapped_column(String(120), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    full_text: Mapped[str | None] = mapped_column(Text)
    version: Mapped[str | None] = mapped_column(String(50))

    redistribution: Mapped[RedistributionPermission] = mapped_column(
        Enum(RedistributionPermission, name="redistribution_permission"),
        nullable=False,
        default=RedistributionPermission.UNKNOWN,
    )
    commercial_use_permitted: Mapped[bool | None] = mapped_column()
    derivative_works_permitted: Mapped[bool | None] = mapped_column()
    share_alike_required: Mapped[bool | None] = mapped_column()
    attribution_required: Mapped[bool | None] = mapped_column()

    # Free text quoted from the licence, used verbatim in the message shown when
    # an export or publish is blocked (§20.9).
    restriction_summary: Mapped[str | None] = mapped_column(Text)

    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_method: Mapped[str | None] = mapped_column(Text)

    sources: Mapped[list[RouteSource]] = relationship(back_populates="licence")


class SourceAttribution(Base, UUIDPrimaryKey, Timestamped):
    """Attribution text a source requires, and where it must appear.

    Contour enforces placement rather than leaving it to the page author: the
    same record drives the map credit, the route detail screen, the export
    manifest and the public share page (§20.7).
    """

    __tablename__ = "source_attribution"

    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="CASCADE"), nullable=False
    )
    text: Mapped[str] = mapped_column(Text, nullable=False)
    url: Mapped[str | None] = mapped_column(Text)
    # Any of: "map", "route_detail", "export_manifest", "public_share", "coverage".
    required_surfaces: Mapped[list[str]] = mapped_column(
        ARRAY(String(40)), nullable=False, default=list
    )

    source: Mapped[RouteSource] = relationship(back_populates="attributions")


class RouteSource(Base, UUIDPrimaryKey, Timestamped):
    """One external source of route or segment data.

    Carries every field required by §2.3. Nullable fields are genuinely unknown
    until verified; they are not defaulted, and the Coverage screen shows them
    as unverified rather than assuming a benign value.
    """

    __tablename__ = "route_source"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_route_source_slug"),
        Index("ix_route_source_connector_status", "connector_status"),
    )

    # 1-2. Identity, owner and publisher
    slug: Mapped[str] = mapped_column(String(120), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner: Mapped[str | None] = mapped_column(String(255))
    publisher: Mapped[str | None] = mapped_column(String(255))

    # 3. Official documentation reference
    documentation_url: Mapped[str | None] = mapped_column(Text)
    # How that documentation was read, e.g. "raw.githubusercontent.com" or
    # "web search snippet". Recorded because it bears on how strong the
    # evidence is (§2.1).
    documentation_evidence: Mapped[str | None] = mapped_column(Text)

    # 4-5. Access and authentication
    access_method: Mapped[str | None] = mapped_column(String(120))
    access_url: Mapped[str | None] = mapped_column(Text)
    authentication_method: Mapped[str | None] = mapped_column(String(120))

    # 6-9. Licence, attribution and permitted use
    licence_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_licence.id", ondelete="RESTRICT")
    )
    permitted_uses: Mapped[str | None] = mapped_column(Text)
    redistribution_restrictions: Mapped[str | None] = mapped_column(Text)

    # 10-12. Coverage and content
    coverage_description: Mapped[str | None] = mapped_column(Text)
    coverage_area = mapped_column(Geometry("MULTIPOLYGON", srid=4326, spatial_index=True))
    route_types: Mapped[list[str]] = mapped_column(ARRAY(String(60)), nullable=False, default=list)
    available_attributes: Mapped[list[str]] = mapped_column(
        ARRAY(String(60)), nullable=False, default=list
    )

    # 13-16. Freshness
    update_method: Mapped[str | None] = mapped_column(Text)
    last_source_update: Mapped[date | None] = mapped_column(Date)
    last_successful_import: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_verification_date: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # How old the data may become before the Coverage screen marks it stale and
    # the operator dashboard raises an alert (§6.10).
    staleness_threshold_days: Mapped[int | None] = mapped_column(Integer)

    # 17-19. Quality and operational state
    known_quality_limitations: Mapped[str | None] = mapped_column(Text)
    connector_status: Mapped[ConnectorStatus] = mapped_column(
        Enum(ConnectorStatus, name="connector_status"),
        nullable=False,
        default=ConnectorStatus.NOT_IMPLEMENTED,
    )
    connector_key: Mapped[str | None] = mapped_column(String(120))
    failure_status: Mapped[str | None] = mapped_column(Text)
    failure_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # 20. Contact or application requirement
    contact_requirement: Mapped[str | None] = mapped_column(Text)

    licence: Mapped[SourceLicence | None] = relationship(back_populates="sources")
    attributions: Mapped[list[SourceAttribution]] = relationship(
        back_populates="source", cascade="all, delete-orphan"
    )
    versions: Mapped[list[RouteSourceVersion]] = relationship(back_populates="source")
    imports: Mapped[list[SourceImport]] = relationship(back_populates="source")


class RouteSourceVersion(Base, UUIDPrimaryKey, Timestamped):
    """One version of one route as a given source published it.

    Preserved even when several sources are grouped into a single catalogue
    entry by duplicate detection (§4.5), and preserved when sources disagree
    (§2.9). Nothing here is ever rewritten by a later import; a changed source
    produces a new row.
    """

    __tablename__ = "route_source_version"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "source_record_id",
            "source_checksum",
            name="uq_route_source_version_identity",
        ),
        Index("ix_route_source_version_source", "source_id"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="CASCADE"), nullable=False
    )
    import_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_import.id", ondelete="SET NULL")
    )
    route_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route.id", ondelete="SET NULL")
    )

    # Identifier as the source issues it, not one Contour invents.
    source_record_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source_checksum: Mapped[str] = mapped_column(String(128), nullable=False)

    name: Mapped[str | None] = mapped_column(String(500))
    # The source's own words, stored verbatim. Never paraphrased, because §13.3
    # requires the exact published description to remain available.
    published_description: Mapped[str | None] = mapped_column(Text)
    published_date: Mapped[date | None] = mapped_column(Date)
    development_status: Mapped[str | None] = mapped_column(String(120))

    # Original geometry exactly as received; never replaced by a snapped or
    # normalised version (§6.8).
    geom_original = mapped_column(Geometry("MULTILINESTRING", srid=4326, spatial_index=True))
    geom_normalised = mapped_column(Geometry("MULTILINESTRING", srid=4326, spatial_index=True))
    geom_matched = mapped_column(Geometry("MULTILINESTRING", srid=4326, spatial_index=True))
    # Set when the matched geometry differs from the original by enough to
    # matter, so the difference can be surfaced rather than hidden (§6.9).
    match_deviation_max_m: Mapped[float | None] = mapped_column()
    match_deviation_mean_m: Mapped[float | None] = mapped_column()

    # Location of the archived original response or file, where the licence
    # permits retaining it (§6.7.1).
    raw_payload_uri: Mapped[str | None] = mapped_column(Text)
    raw_payload_withheld_reason: Mapped[str | None] = mapped_column(Text)

    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    transformation_log: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    validation_result: Mapped[dict | None] = mapped_column(JSONB)

    source: Mapped[RouteSource] = relationship(back_populates="versions")
    route: Mapped[Route | None] = relationship(back_populates="source_versions")


class SourceImport(Base, UUIDPrimaryKey, Timestamped):
    """One execution of one connector (§6.6, §6.7).

    Imports are idempotent by ``idempotency_key``: re-running a job that already
    completed returns the existing row instead of duplicating data.
    """

    __tablename__ = "source_import"
    __table_args__ = (
        UniqueConstraint("idempotency_key", name="uq_source_import_idempotency_key"),
        Index("ix_source_import_source_started", "source_id", "started_at"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source.id", ondelete="CASCADE"), nullable=False
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    job_id: Mapped[str | None] = mapped_column(String(120))

    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # "running" | "succeeded" | "failed" | "partial"
    status: Mapped[str] = mapped_column(String(30), nullable=False, default="running")
    # Genuine stage-based progress, never a synthetic percentage (§19.5).
    stage: Mapped[str | None] = mapped_column(String(80))
    stages_completed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    stages_total: Mapped[int | None] = mapped_column(Integer)

    records_seen: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    records_rejected: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    request_url: Mapped[str | None] = mapped_column(Text)
    response_checksum: Mapped[str | None] = mapped_column(String(128))
    # The licence as it stood at import time, so a later licence change cannot
    # retroactively legitimise or invalidate what was done with this data.
    licence_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("source_licence.id", ondelete="RESTRICT")
    )
    attribution_snapshot: Mapped[str | None] = mapped_column(Text)

    error_code: Mapped[str | None] = mapped_column(String(80))
    error_detail: Mapped[str | None] = mapped_column(Text)
    transformation_log: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    source: Mapped[RouteSource] = relationship(back_populates="imports")


class SourceDiscrepancy(Base, UUIDPrimaryKey, Timestamped):
    """A recorded disagreement between two sources (§2.9).

    Contour does not resolve disagreements by discarding one side. Both source
    versions are kept, the difference is quantified, and ``resolution_used``
    records which one a given result was computed from so the user can see the
    choice that was made.
    """

    __tablename__ = "source_discrepancy"
    __table_args__ = (
        CheckConstraint(
            "left_version_id <> right_version_id",
            name="different_versions",
        ),
        Index("ix_source_discrepancy_route", "route_id"),
    )

    route_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route.id", ondelete="CASCADE")
    )
    left_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("route_source_version.id", ondelete="CASCADE"),
        nullable=False,
    )
    right_version_id: Mapped[uuid.UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("route_source_version.id", ondelete="CASCADE"),
        nullable=False,
    )

    # "geometry" | "name" | "description" | "attribute" | "extent" | "date"
    kind: Mapped[str] = mapped_column(String(40), nullable=False)
    attribute_key: Mapped[str | None] = mapped_column(String(80))
    left_value: Mapped[str | None] = mapped_column(Text)
    right_value: Mapped[str | None] = mapped_column(Text)

    max_separation_m: Mapped[float | None] = mapped_column()
    overlap_ratio: Mapped[float | None] = mapped_column()
    length_difference_m: Mapped[float | None] = mapped_column()
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Which side downstream results actually used. Never implies the other side
    # is wrong — only that this is what was computed from.
    resolution_used_version_id: Mapped[uuid.UUID | None] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("route_source_version.id", ondelete="SET NULL")
    )
    resolution_reason: Mapped[str | None] = mapped_column(Text)
