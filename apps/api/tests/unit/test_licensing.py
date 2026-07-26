"""Licence compatibility and source registry tests (§20.10).

The rule under test is that permission is never assumed. An unverified licence
must block redistribution, and a refusal must name the source and quote the
restriction so the user can tell whether it is fixable.
"""

from __future__ import annotations

from contour_api.models.enums import ConnectorStatus, RedistributionPermission
from contour_api.sources.licensing import (
    Action,
    AttributionManifest,
    SourceTerms,
    evaluate,
)
from contour_api.sources.registry import REGISTRY, CoverageReport, entry

OSM = SourceTerms(
    source_slug="openstreetmap",
    source_name="OpenStreetMap",
    licence_identifier="ODbL-1.0",
    licence_name="Open Database License 1.0",
    redistribution=RedistributionPermission.PERMITTED_SHARE_ALIKE,
    attribution_text="© OpenStreetMap contributors",
    share_alike_required=True,
    contribution_m=120_000.0,
)

UNVERIFIED = SourceTerms(
    source_slug="copernicus-dem-glo-30",
    source_name="Copernicus DEM GLO-30",
    licence_identifier="copernicus-dem-eula",
    licence_name="Copernicus DEM licence",
    redistribution=RedistributionPermission.UNKNOWN,
    contribution_m=120_000.0,
)

PROHIBITED = SourceTerms(
    source_slug="restricted-source",
    source_name="A restricted dataset",
    licence_identifier="proprietary",
    licence_name="Proprietary licence",
    redistribution=RedistributionPermission.PROHIBITED,
    restriction_summary="redistribution of derived geometry is not permitted",
    contribution_m=8_000.0,
)

NON_COMMERCIAL = SourceTerms(
    source_slug="nc-source",
    source_name="A non-commercial dataset",
    licence_identifier="CC-BY-NC-4.0",
    licence_name="Creative Commons Attribution-NonCommercial 4.0",
    redistribution=RedistributionPermission.NON_COMMERCIAL_ONLY,
    restriction_summary="commercial use is not permitted",
)


class TestRedistribution:
    def test_viewing_is_always_permitted(self) -> None:
        """Holding data lawfully and looking at it is not redistribution."""
        decision = evaluate(Action.VIEW, [OSM, PROHIBITED, UNVERIFIED])

        assert decision.allowed

    def test_an_unverified_licence_blocks_export(self) -> None:
        """Silence about terms is not permission.

        A wrong "yes" here has the user republishing data they may have no right
        to, so unverified terms are treated as restrictive.
        """
        decision = evaluate(Action.EXPORT, [OSM, UNVERIFIED])

        assert not decision.allowed
        assert decision.blocking[0].source_slug == "copernicus-dem-glo-30"
        assert "not been verified" in decision.reason

    def test_an_unverified_refusal_does_not_blame_the_publisher(self) -> None:
        """The gap is Contour's, and the message must say so."""
        decision = evaluate(Action.EXPORT, [UNVERIFIED])

        assert "limitation of Contour's source registry" in decision.reason

    def test_a_prohibited_source_blocks_publish_and_names_itself(self) -> None:
        decision = evaluate(Action.PUBLISH, [OSM, PROHIBITED])

        assert not decision.allowed
        assert decision.blocking[0].source_name == "A restricted dataset"
        # The restriction is quoted so the user can judge whether removing that
        # section would fix it.
        assert "redistribution of derived geometry is not permitted" in decision.reason

    def test_non_commercial_permits_export_but_not_commercial_use(self) -> None:
        assert evaluate(Action.EXPORT, [NON_COMMERCIAL]).allowed
        assert not evaluate(Action.COMMERCIAL_USE, [NON_COMMERCIAL]).allowed

    def test_share_alike_propagates_to_the_decision(self) -> None:
        decision = evaluate(Action.PUBLISH, [OSM])

        assert decision.allowed
        assert decision.share_alike_required
        assert "same terms" in decision.reason

    def test_a_route_with_no_external_sources_is_unrestricted(self) -> None:
        decision = evaluate(Action.PUBLISH, [])

        assert decision.allowed

    def test_the_decision_serialises_the_blocking_contribution(self) -> None:
        """The user needs to see how much of the route is affected."""
        payload = evaluate(Action.EXPORT, [OSM, PROHIBITED]).as_dict()

        assert payload["allowed"] is False
        assert payload["blocking_sources"][0]["contribution_m"] == 8_000.0


class TestAttributionManifest:
    def test_manifest_lists_every_attributable_source(self) -> None:
        manifest = AttributionManifest(sources=[OSM])

        text = manifest.render_text()
        assert "© OpenStreetMap contributors" in text
        assert "Open Database License 1.0" in text

    def test_manifest_states_share_alike_obligations(self) -> None:
        manifest = AttributionManifest(sources=[OSM])

        assert "Share-alike" in manifest.render_text()

    def test_manifest_serialises_for_the_export_bundle(self) -> None:
        payload = AttributionManifest(sources=[OSM]).as_dict()

        assert payload["sources"][0]["licence"] == "ODbL-1.0"
        assert payload["text"]


class TestRegistry:
    def test_every_entry_carries_the_required_fields(self) -> None:
        for source in REGISTRY:
            assert source.slug and source.name
            assert source.documentation_evidence, source.slug
            assert source.permitted_uses, source.slug
            assert source.redistribution_restrictions, source.slug
            assert source.known_quality_limitations, source.slug

    def test_unverified_licences_are_marked_and_restricted(self) -> None:
        """An entry cannot claim verified terms and unknown redistribution."""
        for source in REGISTRY:
            if not source.licence_verified:
                assert source.redistribution in {
                    RedistributionPermission.UNKNOWN,
                    RedistributionPermission.PERMITTED_WITH_ATTRIBUTION,
                }, source.slug

    def test_blocked_sources_state_why_rather_than_reporting_no_data(self) -> None:
        """§4.2.8: a blocked host is a stated gap, not an empty region."""
        blocked = [
            s for s in REGISTRY if s.connector_status is ConnectorStatus.IMPLEMENTED_BLOCKED_EGRESS
        ]

        assert blocked
        for source in blocked:
            assert source.failure_status
            assert "egress" in source.failure_status

    def test_the_official_route_is_not_described_as_a_cycling_route(self) -> None:
        """§13.5: the published line is a touring corridor, nothing more."""
        waw = entry("wild-atlantic-way-route")

        assert "not a statement" in waw.known_quality_limitations
        assert "cycling" in waw.known_quality_limitations

    def test_the_elevation_source_documents_its_resolution_limits(self) -> None:
        dem = entry("copernicus-dem-glo-30")

        assert "30 m" in dem.known_quality_limitations
        assert "never samples finer" in dem.known_quality_limitations


class TestCoverage:
    def test_gaps_name_every_source_contributing_nothing(self) -> None:
        report = CoverageReport()

        gaps = {gap["source"] for gap in report.gaps()}
        assert "openstreetmap" in gaps
        assert "tii-national-cycle-network" in gaps
        # The only currently importable source is direct upload.
        assert "user-import" not in gaps

    def test_every_gap_gives_a_reason(self) -> None:
        for gap in CoverageReport().gaps():
            assert gap["reason"], gap["source"]
            assert gap["coverage"], gap["source"]

    def test_coverage_reports_how_little_is_actually_importable(self) -> None:
        """The number that stops Contour implying complete coverage (§4.3)."""
        payload = CoverageReport().as_dict()

        assert payload["importable_count"] < payload["total_count"]
        assert payload["unverified_licences"]
