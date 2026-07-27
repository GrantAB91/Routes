"""Seed ``route_source``, ``source_licence`` and ``source_attribution``.

    uv run --directory apps/api python -m contour_api.seeds.sources

The registry in :mod:`contour_api.sources.registry` is the single definition;
this writes it to the database and ``generate_docs`` writes the same thing to
``docs/source_registry.md``. Neither invents a field the registry does not carry.

Re-running is safe and is the intended way to apply a registry change: rows are
matched by slug and updated in place, so a source's identity — and every import
that points at it — survives.

Licences are seeded only where the registry says the terms were actually
verified. Where they were not, the source is left with no licence row and the
licence engine treats it as restrictive, which is what stops an unverified
source being exported on the strength of a plausible-looking identifier (§20.9).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import dispose_engine, get_session_factory
from ..models.enums import RedistributionPermission
from ..models.source import RouteSource, SourceAttribution, SourceLicence
from ..sources.registry import REGISTRY, RegistryEntry

logger = logging.getLogger(__name__)

# Where each source's attribution has to appear. Every source Contour holds is
# credited on the map and in the export manifest at minimum; §20.7 makes the
# placement Contour's responsibility rather than the page author's.
_DEFAULT_SURFACES = ["map", "route_detail", "export_manifest", "coverage"]


async def _licence(session: AsyncSession, entry: RegistryEntry) -> SourceLicence | None:
    """Upsert the licence row, or return ``None`` when it was never verified.

    A licence row is a positive statement that someone read the terms. Creating
    one from an identifier alone would let the engine treat a guess as a grant.
    """
    if not entry.licence_verified:
        return None

    licence = await session.scalar(
        select(SourceLicence).where(SourceLicence.identifier == entry.licence_identifier)
    )
    if licence is None:
        licence = SourceLicence(identifier=entry.licence_identifier)
        session.add(licence)

    licence.name = entry.licence_name
    licence.url = entry.licence_url
    licence.redistribution = entry.redistribution
    licence.share_alike_required = (
        entry.redistribution is RedistributionPermission.PERMITTED_SHARE_ALIKE
    )
    licence.attribution_required = bool(entry.attribution_text)
    licence.restriction_summary = entry.redistribution_restrictions
    licence.verified_at = datetime.now(UTC)
    licence.verification_method = entry.documentation_evidence
    await session.flush()
    return licence


async def _source(session: AsyncSession, entry: RegistryEntry) -> RouteSource:
    # The licence is resolved *before* the source is attached to the session.
    # Reversing these makes the licence lookup autoflush a half-populated
    # RouteSource, which fails on the not-null columns that have not been set yet.
    licence = await _licence(session, entry)

    source = await session.scalar(select(RouteSource).where(RouteSource.slug == entry.slug))
    if source is None:
        source = RouteSource(slug=entry.slug)
        session.add(source)

    source.name = entry.name
    source.owner = entry.owner
    source.publisher = entry.publisher
    source.documentation_url = entry.documentation_url
    source.documentation_evidence = entry.documentation_evidence
    source.access_method = entry.access_method
    source.access_url = entry.access_url
    source.authentication_method = entry.authentication_method
    source.licence_id = licence.id if licence else None
    source.permitted_uses = entry.permitted_uses
    source.redistribution_restrictions = entry.redistribution_restrictions
    source.coverage_description = entry.coverage_description
    source.route_types = list(entry.route_types)
    source.available_attributes = list(entry.available_attributes)
    source.update_method = entry.update_method
    source.last_source_update = entry.last_source_update
    source.staleness_threshold_days = entry.staleness_threshold_days
    source.known_quality_limitations = entry.known_quality_limitations
    source.connector_status = entry.connector_status
    source.connector_key = entry.connector_key
    source.failure_status = entry.failure_status
    source.failure_observed_at = datetime.now(UTC) if entry.failure_status else None
    source.contact_requirement = entry.contact_requirement
    source.notes = entry.notes or None
    await session.flush()

    await _attribution(session, source, entry)
    return source


async def _attribution(session: AsyncSession, source: RouteSource, entry: RegistryEntry) -> None:
    if not entry.attribution_text:
        return

    existing = await session.scalar(
        select(SourceAttribution).where(
            SourceAttribution.source_id == source.id,
            SourceAttribution.text == entry.attribution_text,
        )
    )
    if existing is None:
        session.add(
            SourceAttribution(
                source_id=source.id,
                text=entry.attribution_text,
                url=entry.licence_url or entry.documentation_url,
                required_surfaces=list(_DEFAULT_SURFACES),
            )
        )


async def seed() -> dict[str, int]:
    factory = get_session_factory()
    async with factory() as session:
        for entry in REGISTRY:
            await _source(session, entry)
        await session.commit()

    return {
        "sources": len(REGISTRY),
        "licences_verified": sum(1 for e in REGISTRY if e.licence_verified),
        "importable": sum(1 for e in REGISTRY if e.is_importable),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed the source registry into PostGIS.")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING)

    async def _main() -> dict[str, int]:
        try:
            return await seed()
        finally:
            await dispose_engine()

    result = asyncio.run(_main())

    print(f"sources seeded    : {result['sources']}")
    print(f"licences verified : {result['licences_verified']}")
    print(f"connectors active : {result['importable']}")
    unverified = result["sources"] - result["licences_verified"]
    if unverified:
        print(
            f"\n{unverified} source(s) have no verified licence and were left "
            "without a licence row. Export and publish of anything derived from "
            "them stays refused until the terms are read from the publisher."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
