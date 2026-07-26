"""Licence compatibility and redistribution control (§20.7-20.10).

A route in Contour is usually a composite: OSM geometry, a government reference
line, an elevation model, maybe an imported track. Each arrives under its own
licence, and the combination is only as permissive as its most restrictive part.

This module answers two questions and refuses to guess at either:

* may this route be published or exported?
* what attribution must appear, and where?

Unknown licensing is treated as restrictive. Where a source's terms have not
been verified, redistribution is refused rather than assumed — the cost of a
wrong "yes" falls on the user, who may republish data they had no right to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from ..models.enums import RedistributionPermission


class Action(StrEnum):
    VIEW = "view"
    EXPORT = "export"
    PUBLISH = "publish"
    COMMERCIAL_USE = "commercial_use"


@dataclass(frozen=True, slots=True)
class SourceTerms:
    """The licence facts Contour needs about one contributing source."""

    source_slug: str
    source_name: str
    licence_identifier: str
    licence_name: str
    redistribution: RedistributionPermission
    attribution_text: str | None = None
    attribution_url: str | None = None
    share_alike_required: bool = False
    commercial_use_permitted: bool | None = None
    # Quoted from the licence, shown verbatim when an action is blocked.
    restriction_summary: str | None = None
    # Distance in metres this source contributes, used to explain which part of
    # a route is restricted rather than only that some of it is.
    contribution_m: float = 0.0


@dataclass(frozen=True, slots=True)
class Decision:
    allowed: bool
    action: Action
    # Sources that caused a refusal, so the user can see exactly which part of
    # the route is the problem.
    blocking: tuple[SourceTerms, ...] = ()
    # Conditions that apply if the action goes ahead.
    share_alike_required: bool = False
    required_attributions: tuple[SourceTerms, ...] = ()
    reason: str = ""

    def as_dict(self) -> dict:
        return {
            "action": self.action.value,
            "allowed": self.allowed,
            "reason": self.reason,
            "share_alike_required": self.share_alike_required,
            "blocking_sources": [
                {
                    "source": s.source_slug,
                    "name": s.source_name,
                    "licence": s.licence_name,
                    "redistribution": s.redistribution.value,
                    "restriction": s.restriction_summary,
                    "contribution_m": s.contribution_m,
                }
                for s in self.blocking
            ],
            "attribution": [
                {
                    "source": s.source_slug,
                    "text": s.attribution_text,
                    "url": s.attribution_url,
                }
                for s in self.required_attributions
                if s.attribution_text
            ],
        }


# Permissions that allow redistribution for a non-commercial purpose such as a
# rider exporting their own route. NON_COMMERCIAL_ONLY belongs here: a
# CC-BY-NC source permits redistribution and restricts only the *purpose*, so
# treating it as non-redistributable would refuse a personal export the licence
# plainly allows.
_REDISTRIBUTABLE = {
    RedistributionPermission.PERMITTED,
    RedistributionPermission.PERMITTED_WITH_ATTRIBUTION,
    RedistributionPermission.PERMITTED_SHARE_ALIKE,
    RedistributionPermission.NON_COMMERCIAL_ONLY,
}


def evaluate(action: Action, sources: list[SourceTerms]) -> Decision:
    """Decide whether an action is permitted across every contributing source.

    Viewing is always allowed: Contour holds the data lawfully, and looking at
    it in the application is not redistribution. Export and publish are where
    data leaves, and both are judged against the most restrictive source.
    """
    if not sources:
        return Decision(
            allowed=True,
            action=action,
            reason="This route has no external source data.",
        )

    attributions = tuple(
        s
        for s in sources
        if s.attribution_text
        and s.redistribution
        in {
            RedistributionPermission.PERMITTED_WITH_ATTRIBUTION,
            RedistributionPermission.PERMITTED_SHARE_ALIKE,
            RedistributionPermission.PERMITTED,
        }
    )

    if action is Action.VIEW:
        return Decision(
            allowed=True,
            action=action,
            required_attributions=attributions,
            reason="Viewing is permitted; attribution is shown with the route.",
        )

    blocking: list[SourceTerms] = []
    for source in sources:
        if source.redistribution is RedistributionPermission.PROHIBITED:
            blocking.append(source)
        elif source.redistribution is RedistributionPermission.UNKNOWN:
            # Unverified terms are treated as restrictive. Assuming permission
            # would put the user at risk of republishing data they may not.
            blocking.append(source)
        elif (
            source.redistribution is RedistributionPermission.NON_COMMERCIAL_ONLY
            and action is Action.COMMERCIAL_USE
        ) or source.redistribution not in _REDISTRIBUTABLE:
            blocking.append(source)

    if blocking:
        return Decision(
            allowed=False,
            action=action,
            blocking=tuple(blocking),
            required_attributions=attributions,
            reason=_refusal_reason(action, blocking),
        )

    share_alike = any(
        s.redistribution is RedistributionPermission.PERMITTED_SHARE_ALIKE or s.share_alike_required
        for s in sources
    )

    return Decision(
        allowed=True,
        action=action,
        share_alike_required=share_alike,
        required_attributions=attributions,
        reason=(
            "Permitted. This route carries a share-alike condition, so anything "
            "derived from it must be offered under the same terms."
            if share_alike
            else "Permitted, with attribution as listed."
        ),
    )


def _refusal_reason(action: Action, blocking: list[SourceTerms]) -> str:
    """Explain a refusal precisely (§20.9).

    Names the source, its licence, and the restriction as published. A message
    that only says "not permitted" leaves the user unable to tell whether the
    problem is fixable — for instance by removing one imported section.
    """
    parts = []
    for source in blocking:
        if source.redistribution is RedistributionPermission.UNKNOWN:
            parts.append(
                f"{source.source_name}: redistribution terms have not been "
                f"verified, so Contour will not {action.value} data derived from "
                "it. This is a limitation of Contour's source registry, not a "
                "statement that the source forbids it."
            )
        else:
            detail = source.restriction_summary or "redistribution is not permitted"
            parts.append(f"{source.source_name} ({source.licence_name}): {detail}.")
    return " ".join(parts)


@dataclass
class AttributionManifest:
    """The attribution block that ships with every export (§15.6.9, §20.7)."""

    sources: list[SourceTerms] = field(default_factory=list)
    generated_by: str = "Contour"

    def render_text(self) -> str:
        lines = [f"Route data assembled by {self.generated_by}.", ""]
        for source in self.sources:
            if not source.attribution_text:
                continue
            line = f"- {source.attribution_text}"
            if source.attribution_url:
                line += f" ({source.attribution_url})"
            line += f" — {source.licence_name}"
            lines.append(line)

        share_alike = [s for s in self.sources if s.share_alike_required]
        if share_alike:
            lines += [
                "",
                "Share-alike: data from "
                + ", ".join(s.source_name for s in share_alike)
                + " requires derived works to be offered under the same licence.",
            ]
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "generated_by": self.generated_by,
            "sources": [
                {
                    "source": s.source_slug,
                    "name": s.source_name,
                    "licence": s.licence_identifier,
                    "licence_name": s.licence_name,
                    "attribution": s.attribution_text,
                    "url": s.attribution_url,
                    "share_alike": s.share_alike_required,
                    "contribution_m": s.contribution_m,
                }
                for s in self.sources
            ],
            "text": self.render_text(),
        }
