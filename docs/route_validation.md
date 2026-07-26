# Route validation

How Contour decides whether a route may be presented as meeting what a rider
asked for. Implementation: `apps/api/src/contour_api/routing/validation.py` and
`routing/constraints.py`. Tests: `tests/unit/test_constraints.py`.

## The four verdicts

| Verdict | Meaning |
| --- | --- |
| `fully_satisfied` | Every requirement was checked against real data and met |
| `satisfied_with_unknown_data` | Nothing was found broken, but some checks could not run |
| `partially_satisfied` | A hard constraint is broken, and the solver has attempts left |
| `not_feasible` | Structurally invalid, or a hard constraint is broken with no attempts left |

The second exists because a route that appears compliant only because the data
needed to check it is missing has not been shown to comply. Collapsing it into
the first is the failure this whole system is built to avoid.

`not_feasible` is only reached after the solve loop has actually tried. Until
then a violation is `partially_satisfied`, because Contour has not yet
established that the request cannot be met — only that it has not met it.

## Structural checks (§7.10)

| Check | Fails when |
| --- | --- |
| `connected_geometry` | Consecutive segments separated by more than 25 m with no ferry to explain it |
| `correct_start_and_finish` | The route starts or ends more than 250 m from the request |
| `direction_restrictions` | A segment is ridden against a one-way restriction |
| `valid_ferry_links` | Water crossings present but not declared as ferry links |
| `valid_route_length` | The route has zero length |
| `elevation_coverage` | Reported, never a failure — a route without elevation is still a route |
| `no_unexplained_water_crossing` | `NOT_AVAILABLE`: no hydrography source is connected |

Tolerances are deliberate. 25 m for connectivity because engines commonly repeat
or drop a shared vertex between edges and an exact-match test produces constant
false gaps. 250 m for endpoints because snapping to the nearest road legitimately
moves a point by tens of metres, more in rural Ireland where a house sits well
back from the network.

## Constraints (§7.4)

Hard constraints make a route infeasible when violated. Preferences only lower a
score. Every constraint reports one of three outcomes, and the third is the point:

- **`MaxGradient`** — segments with no elevation coverage are counted and
  reported as unevaluated, never assumed compliant. An unmeasured segment is
  exactly where a wall is most likely to hide.
- **`MaxSurfaceDistance`** — distance with unknown surface is neither counted
  toward the limit nor away from it. Where the unknown portion could push the
  total past the limit, compliance is unproven; where it could not, compliance is
  provable and reported as satisfied.
- **`MaxUnknownSurfaceDistance`** — always evaluable, because how much is unknown
  is itself known. This is the constraint that lets a rider say "I will accept
  some gravel, but not a mystery".
- **`LegalBicycleAccess`** — access is never inferred. A segment with no recorded
  access is unevaluated, not permitted. Contour will not tell a rider a road is
  legal because nobody recorded that it is not.
- **`ProhibitedRoadClasses`**, **`FerryUse`**, **`MaxTotalDistance`**,
  **`MaxDetour`**.

## Reporting a violation

Every violation names the segments at fault, the requested value, the observed
value, the affected distance, and the rider's own wording where the constraint
came from a written request. That last point matters: an explanation using an
internal key like `max_gradient_percent` is worse than one saying "avoid anything
above 12%", which is what they typed.

Unevaluable constraints produce **no** exclusions for the solver, because absent
data gives nothing to route around.

## Deployment limitations versus route unknowns

A check that cannot run for *any* route is `NOT_AVAILABLE` and is reported as a
standing limitation rather than charged against each route. Only facts missing
about a specific route downgrade its verdict. Without this distinction the
missing hydrography layer alone would make every route in the system report
"satisfied with unknown data", burying the routes where data genuinely is
missing.
