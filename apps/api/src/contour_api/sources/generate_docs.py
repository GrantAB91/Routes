"""Generate ``docs/source_registry.md`` from the registry.

§2.3 requires both a document and a table carrying the same twenty fields. They
are generated from one definition here so they cannot disagree — a registry
maintained in two places drifts within a week, and a source document that
contradicts the database is worse than none.

Run with::

    uv run --directory apps/api python -m contour_api.sources.generate_docs
"""

from __future__ import annotations

import sys
from pathlib import Path

from .registry import REGISTRY, CoverageReport, RegistryEntry

HEADER = """# Source registry

<!--
  GENERATED FILE — do not edit by hand.
  Written by apps/api/src/contour_api/sources/generate_docs.py from the single
  definition in apps/api/src/contour_api/sources/registry.py, which also seeds
  the route_source table. Edit the registry, then regenerate.
-->

Every source Contour can draw on, what it permits, and what it does not
currently supply. §2.3 requires twenty fields per source; they are all below.

Two conventions matter when reading this file:

- **Connector status is not data coverage.** A source marked
  `implemented_blocked_egress` has a working, tested connector and has imported
  nothing, because this deployment's network policy refuses the host. That is a
  stated gap, never an implied absence of routes in that region.
- **An unverified licence blocks redistribution.** Where terms could not be read
  from the publisher, Contour refuses export and publish rather than assuming
  permission. The refusal message says the limitation is Contour's, not the
  publisher's.
"""


def _field(label: str, value: object) -> str:
    if value is None or value == "" or value == ():
        return f"| {label} | _not established_ |"
    if isinstance(value, (list, tuple)):
        return f"| {label} | {', '.join(str(v) for v in value)} |"
    return f"| {label} | {value} |"


def render_entry(source: RegistryEntry) -> str:
    lines = [
        f"### {source.name}",
        "",
        f"`{source.slug}`",
        "",
        "| Field | Value |",
        "| --- | --- |",
        _field("1. Source name", source.name),
        _field("2. Owner / publisher", f"{source.owner or '—'} / {source.publisher or '—'}"),
        _field("3. Documentation", source.documentation_url),
        _field("   Evidence for this entry", source.documentation_evidence),
        _field("4. Access method", source.access_method),
        _field("5. Authentication", source.authentication_method),
        _field(
            "6. Licence",
            f"{source.licence_name} (`{source.licence_identifier}`)"
            + ("" if source.licence_verified else " — **terms not verified**"),
        ),
        _field("7. Required attribution", source.attribution_text),
        _field("8. Permitted uses", source.permitted_uses),
        _field("9. Redistribution restrictions", source.redistribution_restrictions),
        _field("10. Geographic coverage", source.coverage_description),
        _field("11. Route types", source.route_types),
        _field("12. Available attributes", source.available_attributes),
        _field("13. Update method", source.update_method),
        _field("14. Last source update", source.last_source_update),
        _field("15. Last successful import", "never" if not source.is_importable else "—"),
        _field("16. Last verification", source.documentation_evidence),
        _field("17. Known quality limitations", source.known_quality_limitations),
        _field("18. Connector status", f"`{source.connector_status.value}`"),
        _field("19. Failure status", source.failure_status),
        _field("20. Contact / application requirement", source.contact_requirement),
    ]
    if source.notes:
        lines += ["", source.notes]
    return "\n".join(lines)


def render() -> str:
    report = CoverageReport()
    sections = [HEADER, "", "## Summary", ""]

    sections.append(
        f"- {report.as_dict()['importable_count']} of {len(REGISTRY)} sources are "
        "currently importable."
    )
    sections.append(f"- {len(report.gaps())} source(s) contribute nothing; see below.")
    sections.append(
        f"- {len(report.unverified_licences())} licence(s) are unverified and are "
        "therefore refused for export and publish."
    )
    sections += ["", "## Gaps", ""]
    sections.append("| Source | Status | Why |")
    sections.append("| --- | --- | --- |")
    for gap in report.gaps():
        sections.append(f"| {gap['name']} | `{gap['status']}` | {gap['reason']} |")

    sections += ["", "## Sources", ""]
    for source in REGISTRY:
        sections.append(render_entry(source))
        sections.append("")

    return "\n".join(sections).rstrip() + "\n"


def main() -> int:
    target = Path(__file__).resolve().parents[5] / "docs" / "source_registry.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(), encoding="utf-8")
    print(f"wrote {target}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
