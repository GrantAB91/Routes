"""The seeded source registry (§2.3, §4.2).

This module is the single writer of both ``docs/source_registry.md`` and the
``route_source`` table, so the document and the database cannot drift apart.

Every entry records what was actually established, and by what means. Several
sources here are marked ``IMPLEMENTED_BLOCKED_EGRESS``: the connector exists and
is tested, but this deployment's network policy refuses the host, so no data has
been imported. That is a stated gap on the Coverage screen, never an absence of
routes in that region (§4.2.8, §25.18).

Nothing in this file asserts a licence that was not read. Where terms could not
be verified from the publisher directly, ``licence_verified`` is false and
redistribution is treated as restrictive until someone checks.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from ..models.enums import ConnectorStatus, RedistributionPermission


@dataclass(frozen=True, slots=True)
class RegistryEntry:
    """One source, carrying all twenty §2.3 fields."""

    slug: str
    name: str
    owner: str | None
    publisher: str | None
    documentation_url: str | None
    # How the documentation was actually read. Recorded because it bears on how
    # much weight the entry can carry: a publisher's own page is stronger
    # evidence than a search result summarising it.
    documentation_evidence: str
    access_method: str
    access_url: str | None
    authentication_method: str
    licence_identifier: str
    licence_name: str
    licence_url: str | None
    licence_verified: bool
    redistribution: RedistributionPermission
    attribution_text: str | None
    permitted_uses: str
    redistribution_restrictions: str
    coverage_description: str
    route_types: tuple[str, ...]
    available_attributes: tuple[str, ...]
    update_method: str
    last_source_update: date | None
    known_quality_limitations: str
    connector_status: ConnectorStatus
    connector_key: str | None
    failure_status: str | None
    contact_requirement: str | None
    staleness_threshold_days: int | None = None
    notes: str = ""

    @property
    def is_importable(self) -> bool:
        return self.connector_status is ConnectorStatus.ACTIVE


# Evidence channels available in this environment. Recorded verbatim on each
# entry so a reader can judge how firm the entry is.
EVIDENCE_UPSTREAM_DOCS = "upstream project documentation read from its source repository"
EVIDENCE_SEARCH = "web search result summary; publisher page not directly reachable"
EVIDENCE_NOT_VERIFIED = "not verified from the publisher in this environment"

BLOCKED_EGRESS = (
    "The host is refused by this deployment's network egress policy, so no data "
    "has been imported. The connector is implemented and tested against fixtures."
)


REGISTRY: tuple[RegistryEntry, ...] = (
    RegistryEntry(
        slug="openstreetmap",
        name="OpenStreetMap",
        owner="OpenStreetMap contributors",
        publisher="OpenStreetMap Foundation",
        documentation_url="https://www.openstreetmap.org/copyright",
        documentation_evidence=EVIDENCE_SEARCH,
        access_method=(
            "Planet dump streamed from the AWS Open Data mirror and filtered to "
            "Ireland in flight by osmium extract, so the 91 GB source is never "
            "stored (infra/valhalla/extract-from-planet.sh). Geofabrik's regional "
            "extract is the usual route and is refused by this deployment's "
            "egress policy."
        ),
        access_url="https://osm-pds.s3.amazonaws.com/",
        authentication_method="none",
        licence_identifier="ODbL-1.0",
        licence_name="Open Database License 1.0",
        licence_url="https://opendatacommons.org/licenses/odbl/1-0/",
        licence_verified=True,
        redistribution=RedistributionPermission.PERMITTED_SHARE_ALIKE,
        attribution_text="© OpenStreetMap contributors",
        permitted_uses="Use, adaptation and redistribution, including commercially.",
        redistribution_restrictions=(
            "Share-alike: a derived database must be offered under ODbL. "
            "Attribution to OpenStreetMap contributors is required."
        ),
        coverage_description="Global; Contour imports an Ireland and Northern Ireland extract.",
        route_types=("signed cycle routes", "road network", "paths", "ferries"),
        available_attributes=(
            "bicycle access",
            "surface",
            "smoothness",
            "tracktype",
            "cycleway",
            "network membership",
            "maxspeed",
            "highway class",
            "lit",
            "width",
            "bridge",
            "tunnel",
            "ford",
            "barrier",
            "oneway",
            "access conditions",
        ),
        update_method="Full extract replacement; incremental updates supported upstream.",
        last_source_update=None,
        known_quality_limitations=(
            "Coverage of surface and access tags is uneven. Rural Irish lanes are "
            "frequently untagged, which Contour reports as unknown rather than "
            "filling in. Tag meaning varies by contributor and region."
        ),
        connector_status=ConnectorStatus.ACTIVE,
        connector_key="osm_pbf",
        failure_status=None,
        contact_requirement=None,
        staleness_threshold_days=30,
        notes=(
            "Supplies both the routing graph, via Valhalla tiles, and the segment "
            "attributes Contour validates against. The extract is clipped with "
            "osmium's `simple` strategy, which is single-pass and therefore the "
            "only one that works on a stream; ways crossing the bounding box lose "
            "their outside nodes and are dropped, so the box is padded into open "
            "sea and across the border rather than drawn at the coastline."
        ),
    ),
    RegistryEntry(
        slug="wild-atlantic-way-route",
        name="Wild Atlantic Way Route",
        owner="Fáilte Ireland",
        publisher="data.gov.ie (Open Data Unit)",
        documentation_url="https://data.gov.ie/dataset/wild-atlantic-way-route1",
        documentation_evidence=EVIDENCE_SEARCH,
        access_method="ArcGIS Feature Service / dataset download via CKAN",
        access_url="https://data.gov.ie/dataset/wild-atlantic-way-route1",
        authentication_method="none",
        licence_identifier="CC-BY-4.0",
        licence_name="Creative Commons Attribution 4.0",
        licence_url="https://data.gov.ie/pages/opendatalicence",
        # data.gov.ie states CC-BY as the minimum across the portal; the exact
        # terms attached to this dataset have not been read from the publisher
        # in this environment, so the entry does not claim they were.
        licence_verified=False,
        redistribution=RedistributionPermission.PERMITTED_WITH_ATTRIBUTION,
        attribution_text="Contains data from Fáilte Ireland, via data.gov.ie",
        permitted_uses="Reuse and redistribution with attribution, per the portal licence.",
        redistribution_restrictions="Attribution to the originator is required.",
        coverage_description="The Wild Atlantic Way touring route along the west coast of Ireland.",
        route_types=("official touring route",),
        available_attributes=("geometry", "route name", "stage identifiers"),
        update_method="Manual re-import; the publisher does not document a change feed.",
        last_source_update=None,
        known_quality_limitations=(
            "The published line is a driving touring corridor. It is not a "
            "statement that the roads it follows are legal, suitable or safe for "
            "cycling, and Contour never treats it as one (§13.5)."
        ),
        connector_status=ConnectorStatus.IMPLEMENTED_BLOCKED_EGRESS,
        connector_key="arcgis_feature_service",
        failure_status=BLOCKED_EGRESS,
        contact_requirement=None,
        staleness_threshold_days=365,
    ),
    RegistryEntry(
        slug="wild-atlantic-way-discovery-points",
        name="Wild Atlantic Way Signature Discovery Points",
        owner="Fáilte Ireland",
        publisher="data.gov.ie (Open Data Unit)",
        documentation_url="https://data.gov.ie/dataset/wild-atlantic-way1",
        documentation_evidence=EVIDENCE_SEARCH,
        access_method="ArcGIS Feature Service",
        access_url=None,
        authentication_method="none",
        licence_identifier="CC-BY-4.0",
        licence_name="Creative Commons Attribution 4.0",
        licence_url="https://data.gov.ie/pages/opendatalicence",
        licence_verified=False,
        redistribution=RedistributionPermission.PERMITTED_WITH_ATTRIBUTION,
        attribution_text="Contains data from Fáilte Ireland, via data.gov.ie",
        permitted_uses="Reuse and redistribution with attribution.",
        redistribution_restrictions="Attribution to the originator is required.",
        coverage_description="Signature Discovery Points along the Wild Atlantic Way.",
        route_types=("points of interest",),
        available_attributes=("geometry", "name", "published description"),
        update_method="Manual re-import.",
        last_source_update=None,
        known_quality_limitations=(
            "Points mark visitor attractions. Their presence says nothing about "
            "services, water, food or shelter, and Contour does not present them "
            "as such."
        ),
        connector_status=ConnectorStatus.IMPLEMENTED_BLOCKED_EGRESS,
        connector_key="arcgis_feature_service",
        failure_status=BLOCKED_EGRESS,
        contact_requirement=None,
        staleness_threshold_days=365,
    ),
    RegistryEntry(
        slug="copernicus-dem-glo-30",
        name="Copernicus DEM GLO-30",
        owner="European Space Agency",
        publisher="European Union / ESA",
        documentation_url="https://copernicus-dem-30m.s3.amazonaws.com/readme.html",
        documentation_evidence=(
            "publisher readme and tileList.txt read directly from the S3 bucket; "
            "tile geometry and resolution verified by opening the rasters"
        ),
        access_method="Public S3 bucket of Cloud-Optimised GeoTIFF tiles",
        access_url="https://copernicus-dem-30m.s3.amazonaws.com/",
        authentication_method="none for the public mirror",
        licence_identifier="copernicus-dem-eula",
        licence_name="Copernicus DEM licence",
        licence_url=None,
        licence_verified=False,
        redistribution=RedistributionPermission.UNKNOWN,
        attribution_text="© DLR e.V. 2010-2014, © Airbus Defence and Space GmbH",
        permitted_uses="Not established in this environment.",
        redistribution_restrictions=(
            "Not established. Contour therefore refuses to export or publish "
            "elevation values derived from this source until the terms are read "
            "from the publisher and recorded here."
        ),
        coverage_description="Global 30 m digital elevation model.",
        route_types=(),
        available_attributes=("elevation",),
        update_method="Static release; replaced wholesale on a new release.",
        last_source_update=None,
        known_quality_limitations=(
            "Measured against the installed tiles rather than assumed. The grid "
            "is not a uniform 30 m: latitude spacing is one arc second (30.9 m) "
            "and longitude spacing is 1.5 arc seconds in the 50-60 degree band, "
            "so the limiting cell over Ireland is 30.9 m and Contour clamps "
            "sampling to it. This cannot resolve short steep ramps. It is also a "
            "surface model rather than a terrain model: vegetation and buildings "
            "read high, while sharp summits read LOW because a 30 m cell averages "
            "the peak away - Croagh Patrick reads 6 m below its published height "
            "and Mweelrea 45 m below. The second effect is the larger one and "
            "matters most when judging a col. See docs/elevation_method.md."
        ),
        connector_status=ConnectorStatus.ACTIVE,
        connector_key="dem_raster",
        failure_status=None,
        contact_requirement=(
            "Tiles are imported and elevation is served, but the licence terms "
            "have still not been read from the publisher, so export and publish "
            "of derived elevation figures remain refused. Reading them is the "
            "single change that would unblock export."
        ),
        staleness_threshold_days=None,
    ),
    RegistryEntry(
        slug="eurovelo-1",
        name="EuroVelo 1 (Atlantic Coast Route)",
        owner="European Cyclists' Federation",
        publisher="European Cyclists' Federation",
        documentation_url="https://en.eurovelo.com/ev1",
        documentation_evidence=EVIDENCE_NOT_VERIFIED,
        access_method="Not established",
        access_url=None,
        authentication_method="unknown",
        licence_identifier="unknown",
        licence_name="Not established",
        licence_url=None,
        licence_verified=False,
        redistribution=RedistributionPermission.UNKNOWN,
        attribution_text=None,
        permitted_uses="Not established.",
        redistribution_restrictions="Not established; treated as restrictive.",
        coverage_description="Atlantic coast of Europe, including the west of Ireland.",
        route_types=("international cycle route",),
        available_attributes=(),
        update_method="Not established.",
        last_source_update=None,
        known_quality_limitations=(
            "Contour currently derives EuroVelo 1 membership from OpenStreetMap "
            "route relations rather than from the ECF directly. Those are two "
            "different sources and may disagree; where both are present the "
            "disagreement is recorded rather than resolved (§2.9)."
        ),
        connector_status=ConnectorStatus.NOT_IMPLEMENTED,
        connector_key=None,
        failure_status=None,
        contact_requirement=(
            "Machine-readable access and licence terms need to be established "
            "with the ECF before this source can be imported."
        ),
        staleness_threshold_days=None,
    ),
    RegistryEntry(
        slug="tii-national-cycle-network",
        name="TII National Cycle Network",
        owner="Transport Infrastructure Ireland",
        publisher="Transport Infrastructure Ireland",
        documentation_url=None,
        documentation_evidence=EVIDENCE_NOT_VERIFIED,
        access_method="Not established",
        access_url=None,
        authentication_method="unknown",
        licence_identifier="unknown",
        licence_name="Not established",
        licence_url=None,
        licence_verified=False,
        redistribution=RedistributionPermission.UNKNOWN,
        attribution_text=None,
        permitted_uses="Not established.",
        redistribution_restrictions="Not established; treated as restrictive.",
        coverage_description="Republic of Ireland national cycle network.",
        route_types=("national cycle network",),
        available_attributes=(),
        update_method="Not established.",
        last_source_update=None,
        known_quality_limitations="Unassessed; no data has been imported.",
        connector_status=ConnectorStatus.NOT_IMPLEMENTED,
        connector_key=None,
        failure_status=None,
        contact_requirement=(
            "Machine access and licence terms need confirming with TII. Until "
            "then this source contributes nothing and is shown as a gap."
        ),
        staleness_threshold_days=None,
    ),
    RegistryEntry(
        slug="user-import",
        name="User imported files",
        owner="The uploading user",
        publisher="n/a",
        documentation_url=None,
        documentation_evidence="format specifications implemented and tested in contour_api.io",
        access_method="Direct upload (GPX, TCX, KML, GeoJSON, FIT)",
        access_url=None,
        authentication_method="user session",
        licence_identifier="user-owned",
        licence_name="Owned by the uploading user",
        licence_url=None,
        licence_verified=True,
        redistribution=RedistributionPermission.PERMITTED,
        attribution_text=None,
        permitted_uses="Private use by the uploading user; sharing is their decision.",
        redistribution_restrictions=(
            "Contour makes no claim over uploaded files. Imported routes are "
            "private by default and are never published without an explicit act "
            "by their owner (§14.4)."
        ),
        coverage_description="Wherever the user has been or planned.",
        route_types=("recorded tracks", "planned routes"),
        available_attributes=("geometry", "elevation where recorded", "timestamps"),
        update_method="Per upload.",
        last_source_update=None,
        known_quality_limitations=(
            "Recorded tracks carry GNSS error and barometric drift. Contour keeps "
            "the original geometry and shows any map-matched version separately "
            "rather than replacing it (§6.8, §15.5)."
        ),
        connector_status=ConnectorStatus.ACTIVE,
        connector_key="file_upload",
        failure_status=None,
        contact_requirement=None,
        staleness_threshold_days=None,
    ),
)


@dataclass(frozen=True, slots=True)
class CoverageReport:
    """What the Coverage screen renders (§4.2)."""

    entries: tuple[RegistryEntry, ...] = field(default=REGISTRY)

    def by_status(self) -> dict[str, list[str]]:
        grouped: dict[str, list[str]] = {}
        for entry in self.entries:
            grouped.setdefault(entry.connector_status.value, []).append(entry.slug)
        return grouped

    def gaps(self) -> list[dict[str, str]]:
        """Sources contributing nothing, and precisely why.

        This is what stops Contour implying complete coverage. Every entry here
        is a region or attribute set the catalogue does not have.
        """
        return [
            {
                "source": entry.slug,
                "name": entry.name,
                "status": entry.connector_status.value,
                "reason": entry.failure_status or entry.contact_requirement or "not implemented",
                "coverage": entry.coverage_description,
            }
            for entry in self.entries
            if not entry.is_importable
        ]

    def unverified_licences(self) -> list[str]:
        """Sources whose terms nobody has confirmed.

        Redistribution from these is refused, so this list is also the list of
        work needed to make more of the catalogue exportable.
        """
        return [e.slug for e in self.entries if not e.licence_verified]

    def as_dict(self) -> dict:
        return {
            "sources": [
                {
                    "slug": e.slug,
                    "name": e.name,
                    "owner": e.owner,
                    "publisher": e.publisher,
                    "documentation_url": e.documentation_url,
                    "documentation_evidence": e.documentation_evidence,
                    "access_method": e.access_method,
                    "authentication_method": e.authentication_method,
                    "licence": e.licence_name,
                    "licence_identifier": e.licence_identifier,
                    "licence_verified": e.licence_verified,
                    "redistribution": e.redistribution.value,
                    "attribution": e.attribution_text,
                    "permitted_uses": e.permitted_uses,
                    "redistribution_restrictions": e.redistribution_restrictions,
                    "coverage": e.coverage_description,
                    "route_types": list(e.route_types),
                    "available_attributes": list(e.available_attributes),
                    "update_method": e.update_method,
                    "last_source_update": (
                        e.last_source_update.isoformat() if e.last_source_update else None
                    ),
                    "known_quality_limitations": e.known_quality_limitations,
                    "connector_status": e.connector_status.value,
                    "failure_status": e.failure_status,
                    "contact_requirement": e.contact_requirement,
                }
                for e in self.entries
            ],
            "by_status": self.by_status(),
            "gaps": self.gaps(),
            "unverified_licences": self.unverified_licences(),
            "importable_count": sum(1 for e in self.entries if e.is_importable),
            "total_count": len(self.entries),
        }


def entry(slug: str) -> RegistryEntry:
    for candidate in REGISTRY:
        if candidate.slug == slug:
            return candidate
    raise KeyError(slug)
