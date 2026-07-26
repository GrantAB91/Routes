# Licensing and attribution

## The rule

A route in Contour is usually a composite: OpenStreetMap geometry, a government
reference line, an elevation model, perhaps an imported track. The combination is
only as permissive as its most restrictive part.

**Unverified terms are treated as restrictive.** Where a source's licence has not
been read and recorded, export and publish are refused. The cost of a wrong "yes"
falls on the user, who may redistribute data they had no right to.

The refusal message says which source, quotes the restriction, and reports how
much route distance it affects — so the user can tell whether removing one
imported section would fix it. Where the block is caused by unverified terms, the
message says explicitly that this is a limitation of Contour's registry, **not**
a statement that the publisher forbids reuse.

## Actions

| Action | Rule |
| --- | --- |
| View | Always permitted. Holding data lawfully and looking at it is not redistribution |
| Export | Judged against the most restrictive contributing source |
| Publish | As export |
| Commercial use | Additionally blocked by non-commercial licences |

A CC-BY-NC source permits redistribution and restricts only the *purpose*, so a
personal export is allowed and commercial use is not. Treating it as
non-redistributable refuses something the licence plainly allows — a bug this
codebase had until a test caught it.

## Share-alike

ODbL and similar licences propagate. Where any contributing source carries a
share-alike condition, the decision reports it and the export manifest states
that derived works must be offered under the same terms.

## Attribution

Attribution is enforced by placement rather than left to the page author. One
record drives the map credit, the route detail screen, the export manifest and
the public share page. Every export carries a manifest naming each source, its
licence, its attribution text and how much of the route it contributed.

## Current source terms

Generated from the registry into [source_registry.md](source_registry.md). At the
time of writing, **5 of 7 sources have unverified licence terms** and are
therefore refused for export and publish. Verifying them is the single highest
-value piece of work for making the catalogue usable.
