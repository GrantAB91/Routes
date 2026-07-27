"""SQLAlchemy models for Contour.

Importing this package registers every model on :class:`Base.metadata`, which is
what Alembic autogenerate reads. Adding a model without exporting it here means
it silently never gets a migration.
"""

from .analysis import Climb, ElevationSample, RouteScore, ValidationResult
from .base import Base
from .enums import (
    BicycleAccess,
    BicycleType,
    ConnectorStatus,
    CycleLaneKind,
    FeasibilityVerdict,
    GeneralisedSurface,
    GeometryKind,
    KnowledgeStatus,
    RedistributionPermission,
    RoadClass,
    RouteOriginKind,
    RouteVisibility,
    StageBalanceStrategy,
    SurfaceFamily,
)
from .intent import RouteConstraint, RouteIntent
from .jobs import ExportJob, ImportJob, UserReport
from .journey import JourneyProject, PointOfInterest, Stage
from .network import NetworkWay
from .route import (
    AvoidArea,
    BicycleRouteMembership,
    Route,
    RouteSegment,
    RouteVariant,
    RouteVersion,
    SegmentAttribute,
    Waypoint,
)
from .source import (
    RouteSource,
    RouteSourceVersion,
    SourceAttribution,
    SourceDiscrepancy,
    SourceImport,
    SourceLicence,
)
from .user import ConnectedAccount, Organisation, User

__all__ = [
    "AvoidArea",
    "Base",
    "BicycleAccess",
    "BicycleRouteMembership",
    "BicycleType",
    "Climb",
    "ConnectedAccount",
    "ConnectorStatus",
    "CycleLaneKind",
    "ElevationSample",
    "ExportJob",
    "FeasibilityVerdict",
    "GeneralisedSurface",
    "GeometryKind",
    "ImportJob",
    "JourneyProject",
    "KnowledgeStatus",
    "NetworkWay",
    "Organisation",
    "PointOfInterest",
    "RedistributionPermission",
    "RoadClass",
    "Route",
    "RouteConstraint",
    "RouteIntent",
    "RouteOriginKind",
    "RouteScore",
    "RouteSegment",
    "RouteSource",
    "RouteSourceVersion",
    "RouteVariant",
    "RouteVersion",
    "RouteVisibility",
    "SegmentAttribute",
    "SourceAttribution",
    "SourceDiscrepancy",
    "SourceImport",
    "SourceLicence",
    "Stage",
    "StageBalanceStrategy",
    "SurfaceFamily",
    "User",
    "UserReport",
    "ValidationResult",
    "Waypoint",
]
