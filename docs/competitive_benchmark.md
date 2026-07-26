# Competitive benchmark

## Status: method defined, measurements not taken

§17.7 forbids claiming Contour is the best system until measurable testing
supports it. No such testing has been done, so this document defines the method
and records nothing else. **No comparative claim in this repository is supported
by measurement.**

The benchmark requires lawful direct use of the products being compared, which
needs network access the reference environment does not have, and a working
Contour route to compare against, which needs routing tiles.

## Method

Twelve core tasks (§17.3), each performed by the same person on each product,
recording nine measures (§17.4).

### Tasks

1. Find an existing long distance route
2. Generate a route from a written request
3. Create a route manually
4. Drag one section to change it
5. Minimise ascent
6. Avoid a steep climb
7. Inspect surface and access
8. Compare alternatives
9. Split a journey into days
10. Display the route in 3D and topographic views
11. Export a route
12. Recover an accidental edit

### Measures

Number of actions · time to completion · points of confusion · missing
information · error recovery · mobile usability · accessibility · expert control
· source transparency.

## Rules

- Public product documentation and lawful direct use only.
- No proprietary interface, route data, text or visual asset is copied (§17.6).
- No competitor's route data is imported or redistributed.
- Results are published with the date and the product versions tested, because a
  benchmark without them ages into a false claim.

## What Contour is designed to do better

Stated as design intent, **not** as measured results:

- **Source transparency.** Every figure names the source, the licence, the import
  time and the geometry version. Coverage gaps are listed rather than implied.
- **Missing data.** Unknown is reported as its own share of route distance rather
  than defaulted, and a constraint that could not be checked is reported as
  unchecked.
- **Expert control.** Hard constraints are distinguished from preferences, and a
  route that cannot meet a hard constraint says which segments break it.

Whether any of this reduces actions or time for a real rider is untested.
